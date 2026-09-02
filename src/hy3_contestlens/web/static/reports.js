// Only report pages load this script. GET polling never starts translation work.
(() => {
  const actionTriggers = [...document.querySelectorAll(".run-action-trigger")];
  const actionMenus = actionTriggers.map((button) => ({
    button, menu: document.getElementById(button.getAttribute("popovertarget")),
  })).filter((item) => item.menu);

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

  actionMenus.forEach(({button, menu}) => {
    button.addEventListener("click", () => {
      actionMenus.forEach((item) => {
        if (item.menu !== menu && item.menu.matches(":popover-open")) item.menu.hidePopover();
      });
    });
    menu.addEventListener("toggle", (event) => {
      const open = event.newState === "open";
      button.setAttribute("aria-expanded", String(open));
      if (open) requestAnimationFrame(() => positionMenu(button, menu));
    });
  });
  window.addEventListener("resize", () => actionMenus.forEach(({menu}) => menu.matches(":popover-open") && menu.hidePopover()));
  window.addEventListener("scroll", () => actionMenus.forEach(({menu}) => menu.matches(":popover-open") && menu.hidePopover()), true);

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
