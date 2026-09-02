// Only report pages load this script. GET polling never starts translation work.
(() => {
  const actionTriggers = [...document.querySelectorAll(".run-action-trigger")];
  const actionMenus = actionTriggers.map((button) => ({
    button, menu: document.getElementById(button.dataset.actionMenu),
  })).filter((item) => item.menu);
  let activeMenu = null;

  function positionMenu(button, menu) {
    const rect = button.getBoundingClientRect();
    const width = menu.offsetWidth || 220;
    const height = menu.offsetHeight || 180;
    const left = Math.max(12, Math.min(rect.right - width, window.innerWidth - width - 12));
    const below = rect.bottom + 8;
    const top = below + height <= window.innerHeight - 12 ? below : Math.max(12, rect.top - height - 8);
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
  }

  function closeActionMenu({restoreFocus = false} = {}) {
    if (!activeMenu) return;
    const {button, menu} = activeMenu;
    menu.hidden = true;
    menu.classList.remove("is-floating");
    button.setAttribute("aria-expanded", "false");
    activeMenu = null;
    if (restoreFocus) button.focus({preventScroll: true});
  }

  function openActionMenu(button, menu) {
    closeActionMenu();
    document.body.append(menu);
    menu.hidden = false;
    menu.classList.add("is-floating");
    button.setAttribute("aria-expanded", "true");
    activeMenu = {button, menu};
    positionMenu(button, menu);
    menu.querySelector("a[href],button:not(:disabled)")?.focus({preventScroll: true});
  }

  actionMenus.forEach(({button, menu}) => {
    button.addEventListener("click", () => {
      if (activeMenu?.menu === menu) closeActionMenu({restoreFocus: true});
      else openActionMenu(button, menu);
    });
  });
  document.addEventListener("pointerdown", (event) => {
    if (activeMenu && !activeMenu.menu.contains(event.target) && !activeMenu.button.contains(event.target)) closeActionMenu();
  });
  document.addEventListener("focusin", (event) => {
    if (activeMenu && !activeMenu.menu.contains(event.target) && !activeMenu.button.contains(event.target)) closeActionMenu();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && activeMenu) {
      event.preventDefault();
      closeActionMenu({restoreFocus: true});
    }
  });
  const closeForMovement = () => closeActionMenu();
  document.addEventListener("scroll", closeForMovement, true);
  document.addEventListener("wheel", closeForMovement, {capture: true, passive: true});
  document.addEventListener("touchmove", closeForMovement, {capture: true, passive: true});
  window.addEventListener("resize", closeForMovement);
  window.addEventListener("pagehide", closeForMovement);
  window.visualViewport?.addEventListener("scroll", closeForMovement);
  window.visualViewport?.addEventListener("resize", closeForMovement);

  const bulkBar = document.getElementById("bulk-report-actions");
  const reportsPanel = document.getElementById("reports-panel");
  const multiSelectToggle = document.getElementById("toggle-multi-select");
  const selectAll = document.getElementById("select-all-runs");
  const runSelectors = [...document.querySelectorAll(".run-select")];
  const bulkStatus = document.getElementById("bulk-action-status");

  function selectedRuns() {
    return runSelectors.filter((input) => input.checked);
  }

  function syncBulkActions() {
    if (!bulkBar || !selectAll) return;
    const selected = selectedRuns();
    bulkBar.hidden = selected.length === 0;
    document.getElementById("bulk-selected-count").textContent = String(selected.length);
    selectAll.checked = selected.length > 0 && selected.length === runSelectors.length;
    selectAll.indeterminate = selected.length > 0 && selected.length < runSelectors.length;
    bulkBar.querySelector('[data-bulk-action="delete"]')?.toggleAttribute("disabled", selected.some((item) => item.dataset.terminal !== "true"));
    bulkBar.querySelector('[data-bulk-action="hide"]')?.toggleAttribute("disabled", selected.every((item) => item.dataset.hidden === "true"));
    bulkBar.querySelector('[data-bulk-action="show"]')?.toggleAttribute("disabled", selected.every((item) => item.dataset.hidden !== "true"));
    bulkBar.querySelectorAll("[data-bulk-export]").forEach((button) => button.toggleAttribute("disabled", !selected.some((item) => item.dataset.report === "true")));
  }

  function setSelectionMode(active) {
    if (!reportsPanel || !multiSelectToggle) return;
    reportsPanel.classList.toggle("selection-mode", active);
    multiSelectToggle.setAttribute("aria-pressed", String(active));
    if (!active) {
      runSelectors.forEach((input) => { input.checked = false; });
      if (selectAll) {
        selectAll.checked = false;
        selectAll.indeterminate = false;
      }
    }
    closeActionMenu();
    syncBulkActions();
  }

  multiSelectToggle?.addEventListener("click", () => setSelectionMode(!reportsPanel.classList.contains("selection-mode")));
  setSelectionMode(false);

  selectAll?.addEventListener("change", () => {
    runSelectors.forEach((input) => { input.checked = selectAll.checked; });
    syncBulkActions();
  });
  runSelectors.forEach((input) => input.addEventListener("change", syncBulkActions));

  async function runBulkAction(action) {
    const selected = selectedRuns();
    if (!selected.length) return;
    if (action === "delete" && !confirm(`永久删除所选 ${selected.length} 条运行记录及全部运行文件？此操作无法撤销。`)) return;
    bulkStatus.textContent = "处理中…";
    bulkBar.querySelectorAll("button").forEach((button) => { button.disabled = true; });
    try {
      await api("/api/v1/report-actions", {
        method: "POST",
        body: JSON.stringify({run_ids: selected.map((item) => item.value), action, action_token: bulkBar.dataset.actionToken, confirmation: action === "delete" ? "permanent" : null}),
      });
      location.reload();
    } catch (error) {
      bulkStatus.textContent = error.message;
      syncBulkActions();
    }
  }

  async function exportSelected(format) {
    const selected = selectedRuns();
    if (!selected.length) return;
    bulkStatus.textContent = "正在生成压缩包…";
    try {
      const response = await fetch("/api/v1/report-exports", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({run_ids: selected.map((item) => item.value), format, action_token: bulkBar.dataset.actionToken}),
      });
      if (!response.ok) {
        let error;
        try { error = await response.json(); } catch { error = await response.text(); }
        throw new Error(typeof error === "string" ? error : JSON.stringify(error));
      }
      const link = document.createElement("a");
      link.href = URL.createObjectURL(await response.blob());
      link.download = `contestlens-reports-${format}.zip`;
      document.body.append(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(link.href);
      bulkStatus.textContent = "导出完成";
    } catch (error) {
      bulkStatus.textContent = error.message;
    }
  }

  bulkBar?.querySelectorAll("[data-bulk-action]").forEach((button) => button.addEventListener("click", () => runBulkAction(button.dataset.bulkAction)));
  bulkBar?.querySelectorAll("[data-bulk-export]").forEach((button) => button.addEventListener("click", () => exportSelected(button.dataset.bulkExport)));

  const control = document.getElementById("report-translation-control");
  if (!control || control.dataset.enabled !== "true") return;
  const message = document.getElementById("translation-queue-message");
  const retry = document.getElementById("retry-report-translation");
  const spinner = document.getElementById("report-activity-spinner");
  const labels = {EMPTY: "空", PENDING: "待格式化", QUEUED: "排队中", TRANSLATING: "格式化中", READY: "已格式化", FAILED: "格式化失败"};
  const runId = control.dataset.runId;
  const renderedReady = control.dataset.ready === "true";
  let timer;
  let stopped = false;
  window.addEventListener("pagehide", () => {
    stopped = true;
    clearTimeout(timer);
    setActivity(false);
  });
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) {
      stopped = false;
      poll();
    }
  });

  function setActivity(busy) {
    control.setAttribute("aria-busy", String(busy));
    if (spinner) spinner.hidden = !busy;
  }

  function setRetryBusy(busy) {
    if (!retry) return;
    retry.disabled = busy;
    retry.classList.toggle("is-loading", busy);
    retry.setAttribute("aria-busy", String(busy));
  }

  function update(snapshot) {
    const states = new Map(snapshot.runs.map((item) => [item.run_id, item]));
    document.querySelectorAll("[data-translation-run-id]").forEach((badge) => {
      const state = states.get(badge.dataset.translationRunId);
      if (!state) return;
      badge.dataset.state = state.status;
      badge.setAttribute("aria-busy", String(state.status === "TRANSLATING"));
      badge.textContent = (labels[state.status] || state.label) + (state.position ? ` · 第 ${state.position} 位` : "");
      badge.title = state.updated_at ? `最近处理：${state.updated_at}` : "尚未处理";
    });
    const current = states.get(runId);
    setActivity(current ? ["QUEUED", "TRANSLATING"].includes(current.status) : snapshot.busy);
    if (retry) retry.hidden = current?.status !== "FAILED";
    if (current) {
      if (current.status === "READY" && !renderedReady) {
        stopped = true;
        location.reload();
        return;
      }
      const descriptions = {
        READY: "格式化完成",
        QUEUED: `排队中 · 第 ${current.position || 1} 位`,
        TRANSLATING: `正在格式化 · ${current.completed_segments}/${current.total_segments} 个片段`,
        FAILED: "格式化失败，请重试",
        PENDING: "等待格式化",
        EMPTY: "报告为空",
      };
      message.textContent = descriptions[current.status] || current.label;
    } else {
      const ready = snapshot.runs.filter((item) => item.status === "READY").length;
      const failed = snapshot.runs.filter((item) => item.status === "FAILED").length;
      message.textContent = `${snapshot.busy ? "正在格式化" : "处理结束"} · 已完成 ${ready} 份${failed ? ` · 失败 ${failed} 份` : ""}`;
    }
    if (snapshot.busy && !stopped) timer = setTimeout(poll, 1500);
  }

  function connectionError() {
    setActivity(false);
    document.querySelectorAll("[data-translation-run-id]").forEach((badge) => badge.setAttribute("aria-busy", "false"));
    message.textContent = "状态同步失败，请刷新重试";
  }

  async function poll() {
    if (stopped) return;
    try {
      const snapshot = await api("/api/v1/report-translations");
      if (!stopped) update(snapshot);
    } catch {
      connectionError();
    }
  }

  async function start(payload) {
    clearTimeout(timer);
    setActivity(true);
    setRetryBusy(true);
    message.textContent = "加载中…";
    try {
      const snapshot = await api("/api/v1/report-translations", {
        method: "POST", body: JSON.stringify(payload),
      });
      if (!stopped) update(snapshot);
    } catch {
      connectionError();
    } finally {
      setRetryBusy(false);
    }
  }

  retry?.addEventListener("click", () => start({retry_run_id: runId, priority_run_id: runId}));
  start(control.dataset.priority === "true" ? {priority_run_id: runId} : {});
})();
