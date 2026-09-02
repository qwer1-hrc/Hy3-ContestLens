const TERMINAL_RUN_STATUSES = new Set(["COMPLETED", "FAILED", "CANCELLED"]);
const ACTIVE_RUN_STATUSES = new Set([
  "CREATED", "QUEUED", "INTERRUPTED", "DISCOVERING_RESOURCES", "WAITING_FOR_RESOURCE_CONFIRMATION",
  "ANALYZING", "SOLVING", "REVIEWING", "COMPILING", "JUDGING", "LOCALIZING", "REPAIRING", "REJUDGING",
  "WAITING_FOR_IMAGE_CONFIRMATION", "UNDERSTANDING_IMAGES",
]);
const renderedCheckIds = new Set();
const imageChoicePanels = new Map();

const WORKFLOW_COPY = {
  CREATED: {
    title: "评测已创建",
    description: "已固定题目与修复策略，等待后台工作流接管。",
    command: "create_run(problem_id, repair_options)",
  },
  QUEUED: {
    title: "已进入运行队列",
    description: "调度请求已经持久化，正在等待本服务实例接管。",
    command: "claim_run(lease)",
  },
  INTERRUPTED: {
    title: "运行曾被中断",
    description: "上一个服务进程已停止；系统将从最近的持久化检查点自动恢复。",
    command: "recover_run(checkpoint)",
  },
  ANALYZING: {
    title: "分析题目",
    description: "读取已绑定题面，提取约束、输入输出与可验证的题目规格。",
    command: "resources.read_problem_document → hy3.analyze_problem",
  },
  WAITING_FOR_IMAGE_CONFIRMATION: {
    title: "等待图片理解选择",
    description: "题面含图片、图形或难以提取文字的页面，请在此步骤下选择是否精确理解。超时自动跳过。",
    command: "wait_for_image_choice(use / skip)",
  },
  UNDERSTANDING_IMAGES: {
    title: "精确理解题面图片",
    description: "独立图片模型正在读取图形结构，成功的描述将作为题面补充交给 Hy3。",
    command: "vision.describe → append_untrusted_statement_text",
  },
  IMAGE_DESCRIPTION_READY: {
    title: "图片描述已生成",
    description: "该图片的文字描述已保留，可在下方查看；无法辨认的细节仍需谨慎使用。",
    command: "save_image_description",
  },
  IMAGE_UNDERSTANDING_SKIPPED: {
    title: "图片理解已跳过",
    description: "继续使用原始题面文字，不影响后续解题和评测。",
    command: "continue_with_original_statement",
  },
  IMAGE_UNDERSTANDING_COMPLETED: {
    title: "图片理解阶段结束",
    description: "将成功的描述注入题面；未识别的图片保留警告，工作流继续运行。",
    command: "hy3.analyze_problem(augmented_statement)",
  },
  SOLVING: {
    title: "生成解法",
    description: "推导算法、证明义务、复杂度与 C++ 实现。",
    command: "hy3.solve(problem_spec)",
  },
  REVIEWING: {
    title: "双路盲审",
    description: "算法 Critic 与代码 Critic 并行检查推理步骤和实现映射。",
    command: "algorithm_critic ∥ code_critic",
  },
  COMPILING: {
    title: "编译提交",
    description: "冻结当前代码版本，并在隔离环境中执行确定性编译。",
    command: "judge.compile_cpp(revision)",
  },
  COMPILE_COMPLETED: {
    title: "编译完成",
    description: "编译阶段已返回，可展开查看版本、耗时与诊断信息。",
    command: "compile_result",
  },
  JUDGING: {
    title: "初次 Judge",
    description: "正在逐个运行确定性测试点，统计正确性、耗时与内存。",
    command: "judge.check_answer(all_tests)",
  },
  REJUDGING: {
    title: "修复后 Judge",
    description: "正在对新修订版本重新执行全部确定性测试点。",
    command: "judge.check_answer(repaired_revision)",
  },
  JUDGE_COMPLETED: {
    title: "Judge 完成",
    description: "本轮测试点结果已就绪，完整色块视图显示在工作流下方。",
    command: "check_result",
  },
  JUDGE_SKIPPED: {
    title: "Judge 未执行",
    description: "当前版本未通过编译，因此没有进入测试点运行阶段。",
    command: "skip_check(compile_failed)",
  },
  LOCALIZING: {
    title: "定位错误",
    description: "合并 Critic 与 Judge 证据，定位最早出错步骤。",
    command: "adjudicate(critic_reviews, judge_result)",
  },
  REPAIRING: {
    title: "生成修复",
    description: "围绕当前最佳版本和首个错误点生成有界修复。",
    command: "hy3.repair(best_revision)",
  },
  COMPLETED: {
    title: "工作流完成",
    description: "评测证据、最佳版本与停止原因已经冻结。",
    command: "finalize_run",
  },
  FAILED: {
    title: "工作流失败",
    description: "运行因错误停止，可展开查看结构化错误信息。",
    command: "stop_run(error)",
  },
  CANCELLED: {
    title: "运行已取消",
    description: "用户已请求停止此工作流。",
    command: "cancel_run",
  },
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  let data;
  try {
    data = await response.json();
  } catch {
    data = await response.text();
  }
  if (!response.ok) {
    throw new Error(typeof data === "string" ? data : JSON.stringify(data, null, 2));
  }
  return data;
}

function show(id, value) {
  document.getElementById(id).textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function makeElement(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function validatePath() {
  try {
    show("resource-result", await api("/api/v1/resource-scopes:validate", {
      method: "POST",
      body: JSON.stringify({ path: document.getElementById("resource-path").value }),
    }));
  } catch (error) {
    show("resource-result", error.message);
  }
}

async function grantPath() {
  try {
    const result = await api("/api/v1/resource-scopes", {
      method: "POST",
      body: JSON.stringify({ path: document.getElementById("resource-path").value, confirmed: true }),
    });
    show("resource-result", result);
    location.reload();
  } catch (error) {
    show("resource-result", error.message);
  }
}

async function discoverAssets() {
  const scope = document.getElementById("scope-select").value;
  const problem = document.getElementById("discover-problem").value;
  const target = document.getElementById("candidate-list");
  try {
    const result = await api(`/api/v1/resource-scopes/${scope}:discover`, {
      method: "POST",
      body: JSON.stringify({ problem_id: problem, mode: "auto", search_tests: true }),
    });
    target.innerHTML = result.candidates.map((candidate) => `<div class="candidate"><strong>${candidate.confidence.toFixed(2)} · ${candidate.evidence.join(" + ")}</strong><p>${candidate.document ? `${candidate.document.relative_path} · ${candidate.document.page_start || candidate.document.line_start}-${candidate.document.page_end || candidate.document.line_end}` : "未发现题面"}</p><p>配对 ${candidate.test_dataset?.paired_count || 0}/${candidate.test_dataset?.total_inputs || 0} · 风险 ${candidate.risk.join(", ") || "无"}</p><button onclick="bindCandidate('${scope}','${problem}','${candidate.candidate_id}')">确认绑定</button></div>`).join("") || "<p>未发现候选。</p>";
  } catch (error) {
    target.textContent = error.message;
  }
}

async function bindCandidate(scope, problem, candidate) {
  try {
    const result = await api(`/api/v1/problems/${problem}/resource-binding`, {
      method: "POST",
      body: JSON.stringify({ scope_id: scope, candidate_id: candidate }),
    });
    alert(`已绑定 ${result.binding_id}`);
  } catch (error) {
    alert(error.message);
  }
}

async function createRun() {
  const target = document.getElementById("run-create-result");
  const button = document.getElementById("run-create-button");
  if (button.disabled) return;
  const problem = document.getElementById("run-problem").value;
  button.disabled = true;
  button.classList.add("is-loading");
  button.setAttribute("aria-busy", "true");
  button.textContent = "正在创建并启动…";
  target.textContent = "正在启动…";
  target.classList.add("is-active");
  try {
    const created = await api("/api/v1/runs", {
      method: "POST",
      headers: { "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({
        problem_id: problem,
        image_understanding: "ask",
        repair: {
          enabled: document.getElementById("repair-enabled").checked,
          max_rounds: Number(document.getElementById("repair-rounds").value),
        },
      }),
    });
    await api(`/api/v1/runs/${created.run_id}/start`, { method: "POST" });
    location.assign(`/ui/runs/${created.run_id}`);
  } catch (error) {
    target.textContent = error.message;
    target.classList.remove("is-active");
    button.disabled = false;
    button.textContent = "创建并启动";
  } finally {
    button.classList.remove("is-loading");
    button.setAttribute("aria-busy", "false");
  }
}

async function cancelRun(run) {
  try {
    await api(`/api/v1/runs/${run}/cancel`, { method: "POST" });
  } catch (error) {
    alert(error.message);
  }
}

function formatEventTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value || "—";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

function eventTone(event) {
  if (event.type === "WAITING_FOR_IMAGE_CONFIRMATION") return "warning";
  if (event.type === "IMAGE_UNDERSTANDING_COMPLETED") return event.data?.status === "completed" ? "success" : "warning";
  if (["FAILED", "CANCELLED", "JUDGE_SKIPPED"].includes(event.type)) return "danger";
  if (event.type === "COMPLETED") return "success";
  if (event.type === "COMPILE_COMPLETED") {
    return event.data?.compile?.verdict === "OK" ? "success" : "danger";
  }
  if (event.type === "JUDGE_COMPLETED") {
    return event.data?.check?.verdict === "AC" ? "success" : "warning";
  }
  return "active";
}

function compactEventData(event) {
  const data = { ...(event.data || {}) };
  if (data.check?.tests) {
    data.check = { ...data.check, tests: `[${data.check.tests.length} 个测试点，详见下方面板]` };
  }
  if (data.compile?.diagnostics?.length > 12) {
    data.compile = {
      ...data.compile,
      diagnostics: [...data.compile.diagnostics.slice(0, 12), `…另有 ${data.compile.diagnostics.length - 12} 条`],
    };
  }
  return data;
}

function appendWorkflowEvent(event, runStatus, runId) {
  const list = document.getElementById("event-list");
  const empty = document.getElementById("event-empty");
  if (!list || document.querySelector(`[data-event-seq="${event.seq}"]`)) return;
  if (empty) empty.hidden = true;

  const previousCurrent = list.lastElementChild;
  if (previousCurrent) {
    previousCurrent.classList.remove("is-current");
    previousCurrent.classList.add("is-finished");
    previousCurrent.querySelector(":scope > details")?.removeAttribute("open");
  }

  const copy = WORKFLOW_COPY[event.type] || {
    title: event.type,
    description: "工作流返回了一条新事件。",
    command: event.type.toLowerCase(),
  };
  const row = makeElement("article", `workflow-event tone-${eventTone(event)}`);
  row.dataset.eventSeq = event.seq;
  row.dataset.eventType = event.type;
  const details = makeElement("details", "event-details");
  details.open = true;
  const summary = makeElement("summary", "event-summary");
  const dot = makeElement("span", "event-dot");
  dot.setAttribute("aria-hidden", "true");
  summary.append(dot);
  const main = makeElement("span", "event-main");
  main.append(makeElement("strong", "event-title", copy.title));
  main.append(makeElement("span", "event-description-short", copy.description));
  summary.append(main);
  const meta = makeElement("span", "event-meta");
  const running = makeElement("span", "event-running-tag", "运行中");
  running.hidden = true;
  meta.append(running);
  meta.append(makeElement("span", "event-seq", `#${event.seq}`));
  meta.append(makeElement("time", "event-time", formatEventTime(event.created_at)));
  summary.append(meta);
  details.append(summary);

  const body = makeElement("div", "event-body");
  body.append(makeElement("p", "event-description", copy.description));
  const command = makeElement("div", "event-command");
  command.append(makeElement("span", "event-command-label", "当前指令"));
  command.append(makeElement("code", "", copy.command));
  body.append(command);

  if (event.type === "WAITING_FOR_IMAGE_CONFIRMATION" && runId) {
    const panel = makeElement("div", "image-choice");
    panel.append(makeElement("p", "", `检测到 ${event.data.images.length} 个待理解页面或图片。选择使用后会将这些图片和部分题面文字发送至单独配置的 ${event.data.model}，可能产生额外费用。`));
    panel.append(makeElement("p", "", event.data.images.map((item) => item.label).join("、")));
    const message = makeElement("p", "image-choice-message", `请在 ${event.data.timeout_seconds} 秒内选择；不使用或超时将继续原流程。`);
    message.setAttribute("role", "status");
    const actions = makeElement("div", "actions");
    const buttons = [
      makeElement("button", "primary", "使用更精确的图片理解"),
      makeElement("button", "", "不使用，继续运行"),
    ];
    buttons.forEach((button, index) => {
      button.type = "button";
      button.disabled = runStatus !== "WAITING_FOR_IMAGE_CONFIRMATION";
      button.addEventListener("click", () => chooseImageUnderstanding(runId, event.data.request_id, index === 0 ? "use" : "skip"));
      actions.append(button);
    });
    panel.append(actions, message);
    body.append(panel);
    imageChoicePanels.set(event.data.request_id, {buttons, message, pending: false});
  }
  if (event.type === "IMAGE_DESCRIPTION_READY") {
    body.append(makeElement("pre", "image-description", event.data.text));
  }
  if (event.type === "IMAGE_UNDERSTANDING_SKIPPED") {
    const reasons = {
      no_images: "未检测到需要理解的图片。", no_supported_images: "图片引用不受支持或不在授权范围内。",
      not_configured: "未配置独立图片模型，已自动跳过。", invalid_configuration: "独立图片模型配置无效，已自动跳过。",
      user_skipped: "已选择不使用图片理解。", decision_timeout: "等待选择超时，已自动跳过。",
      inspection_failed: "图片检查未成功，已回退到原始题面。",
    };
    body.append(makeElement("p", "", reasons[event.data.reason] || "已跳过图片理解。"));
  }
  if (event.type === "IMAGE_UNDERSTANDING_COMPLETED" && event.data.status !== "completed") {
    body.append(makeElement("p", "", `成功识别 ${event.data.described} 项；其余项目未识别，已回退到原题面文字。详见结构化参数中的警告。`));
  }

  const payload = compactEventData(event);
  if (Object.keys(payload).length) {
    const payloadDetails = makeElement("details", "event-payload");
    payloadDetails.append(makeElement("summary", "", "查看结构化参数"));
    payloadDetails.append(makeElement("pre", "event-json", JSON.stringify(payload, null, 2)));
    body.append(payloadDetails);
  }
  details.append(body);
  row.append(details);
  list.append(row);

  if (event.type === "JUDGE_COMPLETED" && event.data?.check) {
    renderJudgeCheck(event.data.check, {
      phase: event.data.phase,
      round: event.data.round,
      revisionId: event.data.revision_id,
    });
  }
  updateWorkflowEventState(runStatus);
  const scrollArea = list.closest(".timeline-scroll") || list;
  scrollArea.scrollTo({ top: scrollArea.scrollHeight, behavior: "smooth" });
}

async function chooseImageUnderstanding(runId, requestId, choice) {
  const panel = imageChoicePanels.get(requestId);
  if (!panel || panel.pending || panel.buttons.every((button) => button.disabled)) return;
  panel.pending = true;
  panel.buttons.forEach((button) => { button.disabled = true; });
  panel.message.textContent = "正在提交选择…";
  try {
    await api(`/api/v1/runs/${runId}/image-understanding`, {
      method: "POST", body: JSON.stringify({request_id: requestId, choice}),
    });
    panel.accepted = true;
    panel.message.textContent = choice === "use" ? "已选择精确理解，等待图片模型处理。" : "已跳过，继续运行。";
  } catch (error) {
    panel.message.textContent = `选择未提交：${error.message}`;
    panel.buttons.forEach((button) => { button.disabled = false; });
  } finally {
    panel.pending = false;
  }
}

function syncImageChoices(state) {
  const image = state.image_understanding;
  imageChoicePanels.forEach((panel, requestId) => {
    const active = state.status === "WAITING_FOR_IMAGE_CONFIRMATION" && image?.request_id === requestId && !image.choice;
    panel.buttons.forEach((button) => { button.disabled = !active || panel.pending || panel.accepted; });
    if (!active && !panel.pending) {
      panel.message.textContent = image?.request_id === requestId && image.choice
        ? (image.choice === "use" ? "已选择使用精确图片理解。" : "已跳过图片理解，继续原流程。")
        : "此选择已结束。";
    }
  });
}

function setRunActivity(busy) {
  const current = document.getElementById("workflow-current");
  const spinner = document.getElementById("run-activity-spinner");
  current?.setAttribute("aria-busy", String(busy));
  if (spinner) spinner.hidden = !busy;
}

function updateWorkflowEventState(runStatus) {
  const busy = ACTIVE_RUN_STATUSES.has(runStatus);
  setRunActivity(busy && runStatus !== "WAITING_FOR_IMAGE_CONFIRMATION");
  const list = document.getElementById("event-list");
  if (!list) return;
  const rows = [...list.querySelectorAll(".workflow-event")];
  const active = busy ? rows.filter((row) => row.dataset.eventType === runStatus).at(-1) : null;
  rows.forEach((row) => {
    const running = row === active;
    row.classList.toggle("is-current", running);
    row.classList.toggle("is-finished", !running);
    row.setAttribute("aria-busy", String(running));
    const tag = row.querySelector(".event-running-tag");
    if (tag) {
      tag.hidden = !running;
      tag.textContent = runStatus === "WAITING_FOR_IMAGE_CONFIRMATION" ? "等待选择" : ["CREATED", "QUEUED", "INTERRUPTED"].includes(runStatus) ? "等待中" : "运行中";
    }
  });
}

function setWorkflowDetails(open) {
  document.querySelectorAll("#event-list .event-details").forEach((details) => {
    details.open = open;
  });
}

function setJudgeDetails(open) {
  document.querySelectorAll("#judge-list > .judge-result").forEach((details) => {
    details.open = open;
  });
}

function phaseTitle(meta) {
  if (meta.phase === "repair") return `修复第 ${meta.round || "?"} 轮`;
  return "初次评测";
}

function testDisplayNumber(testId, index) {
  const match = String(testId || "").match(/(\d+)(?!.*\d)/);
  return match ? String(Number(match[1])) : String(index + 1);
}

function verdictClass(verdict) {
  return String(verdict || "UNKNOWN").toLowerCase().replace(/[^a-z0-9_-]/g, "-");
}

function formatMemory(value) {
  const number = Number(value || 0);
  return number >= 10 ? `${number.toFixed(1)}MB` : `${number.toFixed(2)}MB`;
}

function appendTestDetail(parent, label, value) {
  const row = makeElement("div", "test-detail-row");
  row.append(makeElement("span", "", label));
  row.append(makeElement("strong", "", value));
  parent.append(row);
}

function renderJudgeCheck(check, meta = {}) {
  const list = document.getElementById("judge-list");
  const section = document.getElementById("judge-section");
  if (!list || !section || !check) return;
  const checkKey = check.check_id || `${meta.phase || "unknown"}-${meta.round || 0}-${check.total || 0}`;
  if (renderedCheckIds.has(checkKey)) return;
  renderedCheckIds.add(checkKey);

  list.querySelectorAll(":scope > .judge-result").forEach((item) => item.removeAttribute("open"));
  section.hidden = false;
  const details = makeElement("details", "judge-result");
  details.dataset.checkId = checkKey;
  details.open = true;
  const summary = makeElement("summary", "judge-summary");
  const title = makeElement("span", "judge-summary-title");
  title.append(makeElement("span", "judge-kicker", phaseTitle(meta)));
  title.append(makeElement("strong", "", `测试点信息 · ${check.passed ?? 0}/${check.total ?? 0} 通过`));
  summary.append(title);
  const score = makeElement("span", "judge-summary-score");
  score.append(makeElement("span", `verdict verdict-${verdictClass(check.verdict)}`, check.verdict || "—"));
  score.append(makeElement("span", "judge-score", `${check.score ?? 0} 分`));
  summary.append(score);
  details.append(summary);

  const content = makeElement("div", "judge-content");
  const ratio = check.total ? Math.round((check.passed / check.total) * 100) : 0;
  const progress = makeElement("div", "judge-progress");
  const progressBar = makeElement("span", "judge-progress-bar");
  progressBar.style.width = `${ratio}%`;
  progress.append(progressBar);
  content.append(progress);

  const context = makeElement("div", "judge-context");
  context.append(makeElement("code", "", check.check_id || "check pending"));
  if (meta.revisionId) context.append(makeElement("span", "", `版本 ${meta.revisionId}`));
  context.append(makeElement("span", "", `通过率 ${ratio}%`));
  content.append(context);

  const tests = Array.isArray(check.tests) ? check.tests : [];
  if (!tests.length) {
    content.append(makeElement("p", "judge-empty", check.total === 0 ? "本次 Judge 未产生可展示的测试点，通常表示沙箱不可用。" : "测试点详情暂不可用。"));
  } else {
    const grid = makeElement("div", "test-grid");
    tests.forEach((test, index) => {
      const item = makeElement("details", `test-case verdict-${verdictClass(test.verdict)}`);
      const itemSummary = makeElement("summary", "test-case-summary");
      itemSummary.append(makeElement("span", "test-number", `#${testDisplayNumber(test.test_id, index)}`));
      itemSummary.append(makeElement("strong", "test-verdict", test.verdict || "—"));
      itemSummary.append(makeElement("span", "test-metrics", `${test.wall_ms ?? 0}ms / ${formatMemory(test.peak_rss_mb)}`));
      item.append(itemSummary);
      const extra = makeElement("div", "test-extra");
      appendTestDetail(extra, "测试点", test.test_id || `#${index + 1}`);
      appendTestDetail(extra, "CPU", `${test.cpu_ms ?? 0} ms`);
      appendTestDetail(extra, "退出码", test.exit_code ?? "—");
      appendTestDetail(extra, "内存上限", `${test.memory_limit_mb ?? "—"} MB`);
      if (test.first_diff) {
        const diff = makeElement("details", "test-diff");
        diff.append(makeElement("summary", "", "查看首个差异"));
        diff.append(makeElement("pre", "", JSON.stringify(test.first_diff, null, 2)));
        extra.append(diff);
      }
      item.append(extra);
      grid.append(item);
    });
    content.append(grid);
  }
  details.append(content);
  list.append(details);
  details.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function renderJudgeHistory(result) {
  const initial = result?.initial_submission_result?.check;
  if (initial) {
    renderJudgeCheck(initial, {
      phase: "initial",
      revisionId: result.initial_submission_result.revision_id,
    });
  }
  for (const [index, round] of (result?.repair_round_results || []).entries()) {
    if (round.answer_check_result) {
      renderJudgeCheck(round.answer_check_result, {
        phase: "repair",
        round: index + 1,
        revisionId: round.new_revision_id,
      });
    }
  }
}

function updateRunSummary(state) {
  const copy = WORKFLOW_COPY[state.status];
  const status = document.getElementById("run-status");
  status.textContent = copy?.title || state.status;
  status.dataset.status = state.status;
  setRunActivity(ACTIVE_RUN_STATUSES.has(state.status));
  const currentWork = document.getElementById("current-work");
  if (currentWork) currentWork.textContent = copy?.description || "正在同步最新运行状态。";

  if (!state.result) return;
  const initial = state.result.initial_submission_result?.check;
  const best = state.result.best_submission_result?.check;
  document.getElementById("initial-score").textContent = initial ? `${initial.passed}/${initial.total}` : "—";
  document.getElementById("best-score").textContent = best ? `${best.passed}/${best.total}` : "—";
  document.getElementById("stop-reason").textContent = state.result.stop_reason || state.result.error_code || "—";
  show("run-result", state.result);
  renderJudgeHistory(state.result);
}

async function watchRun(run) {
  let cursor = 0;
  while (true) {
    try {
      const state = await api(`/api/v1/runs/${run}`);
      updateRunSummary(state);
      const events = await api(`/api/v1/runs/${run}/events?after_seq=${cursor}`);
      for (const event of events.events) {
        cursor = event.seq;
        appendWorkflowEvent(event, state.status, run);
      }
      updateWorkflowEventState(state.status);
      syncImageChoices(state);
      if (TERMINAL_RUN_STATUSES.has(state.status)) break;
    } catch (error) {
      updateWorkflowEventState("DISCONNECTED");
      show("run-result", error.message);
      const currentWork = document.getElementById("current-work");
      if (currentWork) currentWork.textContent = "无法继续同步运行状态，请检查服务连接。";
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
}

async function loadSubmission(run, submission) {
  const target = document.getElementById("revision-list");
  try {
    const data = await api(`/api/v1/runs/${run}/submissions/${submission}/revisions`);
    target.innerHTML = data.revisions.map((revision) => `<button onclick="loadRevision('${run}','${submission}','${revision.revision_id}')">${revision.revision_id} · ${revision.sha256.slice(0, 10)} · ${revision.change_kind}</button>`).join(" ");
  } catch (error) {
    target.textContent = error.message;
  }
}

async function loadRevision(run, submission, revision) {
  try {
    show("revision-source", await api(`/api/v1/runs/${run}/submissions/${submission}/revisions/${revision}`));
  } catch (error) {
    show("revision-source", error.message);
  }
}
