const modelProgressCalls = new Map();
const MODEL_PHASES = {
  waiting: "等待模型响应", reasoning: "正在推理", generating: "正在生成结果",
  validating: "校验结果", retrying: "等待重试", success: "已完成",
  error: "未完成", cancelled: "已停止", interrupted: "执行已中断", disconnected: "连接中断，待同步",
};
const MODEL_ROLES = {
  problem_analyst: "题目分析", solver: "解法生成", algorithm_critic: "算法评审",
  code_critic: "代码评审", code_critic_recheck: "结合判题证据复核", code_repair_agent: "解法修复",
  public_oracle: "构建独立穷举器", model_rethink: "重新检查算法建模",
};
const MODEL_FAILURES = {
  repetitive_output: "检测到持续重复的评审正文，已提前停止无效生成",
  output_truncated: "结果达到输出上限", transport_error: "模型连接中断或超时",
  stream_incomplete: "响应未完整结束", schema_validation: "结果结构未通过校验",
  json_decode: "结果格式未通过校验", http_error: "模型服务暂未成功响应",
};
const MODEL_DONE = new Set(["success", "error", "cancelled", "interrupted"]);

function progressDuration(milliseconds) {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  return seconds >= 60 ? `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒` : `${seconds} 秒`;
}

function progressNode(tag, className, text = "") {
  const element = document.createElement(tag);
  element.className = className;
  element.textContent = text;
  return element;
}

function appendModelProgress(event) {
  const data = event.data || {};
  if (!Number.isInteger(data.parent_seq) || !data.call_id || !MODEL_PHASES[data.phase]) return;
  const parent = document.querySelector(`[data-event-seq="${data.parent_seq}"]`);
  if (!parent) return;
  let call = modelProgressCalls.get(data.call_id);
  if (!call) {
    let group = parent.querySelector(".model-progress-group");
    if (!group) {
      group = progressNode("div", "model-progress-group");
      group.setAttribute("aria-label", "执行明细");
      parent.querySelector(".event-body").append(group);
      parent.querySelector(".event-main").append(progressNode("span", "model-stage-summary"));
    }
    const details = progressNode("details", "model-call");
    details.open = true;
    const summary = progressNode("summary", "model-call-summary");
    const indicator = progressNode("span", "model-progress-indicator");
    indicator.setAttribute("aria-hidden", "true");
    const label = progressNode("strong", "", MODEL_ROLES[data.role] || "模型调用");
    const status = progressNode("span", "model-call-status");
    const duration = progressNode("span", "model-call-duration");
    summary.append(indicator, label, status, duration);
    const body = progressNode("div", "model-call-body");
    details.append(summary, body);
    group.append(details);
    call = {details, status, duration, body, parent, attempts: new Map(), startedAt: data.started_at};
    modelProgressCalls.set(data.call_id, call);
  }
  // Replayed events and duplicate polling responses must not duplicate nested rows.
  if (call.seq >= event.seq) return;
  call.seq = event.seq;
  call.data = data;
  call.updatedAt = event.created_at;
  let attempt = call.attempts.get(data.attempt);
  if (!attempt) {
    for (const previous of call.attempts.values()) {
      previous.details.classList.remove("is-active");
      previous.line?.classList.remove("is-active");
    }
    const details = progressNode("details", "model-attempt");
    details.open = true;
    const summary = progressNode("summary", "");
    const title = progressNode("span", "", `第 ${data.attempt} 次尝试 / 最多 ${data.max_attempts} 次`);
    const status = progressNode("span", "model-attempt-status");
    summary.append(title, status);
    const body = progressNode("ol", "model-phase-list");
    details.append(summary, body);
    call.body.append(details);
    attempt = {details, status, body, lastPhase: null};
    call.attempts.set(data.attempt, attempt);
  }
  if (attempt.lastPhase !== data.phase) {
    if (attempt.line) attempt.line.classList.remove("is-active");
    attempt.line = progressNode("li", "model-phase");
    attempt.text = progressNode("span", "");
    attempt.line.append(attempt.text);
    attempt.body.append(attempt.line);
    attempt.lastPhase = data.phase;
  }
  const reason = data.failure_kind ? (MODEL_FAILURES[data.failure_kind] || "本次响应未通过检查") : "";
  const retry = data.phase === "retrying" ? `，${data.retry_delay_seconds} 秒后重新尝试` : "";
  const chars = data.answer_chars ? ` · 已接收 ${data.answer_chars.toLocaleString()} 字符` : "";
  attempt.line.classList.toggle("is-warning", ["retrying", "error", "cancelled"].includes(data.phase));
  attempt.text.textContent = `${MODEL_PHASES[data.phase]}${reason ? ` · ${reason}` : ""}${retry}${chars}`;
  attempt.status.textContent = `${MODEL_PHASES[data.phase]} · ${progressDuration(data.elapsed_ms)}`;
}

function updateModelProgressState(runStatus) {
  const now = Date.now();
  const terminal = ["COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"].includes(runStatus);
  const groups = new Map();
  for (const call of modelProgressCalls.values()) {
    let phase = call.data.phase;
    const ended = MODEL_DONE.has(phase);
    if (!ended && terminal) phase = runStatus === "CANCELLED" ? "cancelled" : "interrupted";
    if (!ended && runStatus === "DISCONNECTED") phase = "disconnected";
    const active = !MODEL_DONE.has(phase) && phase !== "disconnected";
    call.details.classList.toggle("is-active", active);
    call.details.classList.toggle("is-error", ["error", "interrupted"].includes(phase));
    call.details.setAttribute("aria-busy", String(active));
    call.status.textContent = MODEL_PHASES[phase];
    const end = ended || !active ? Date.parse(call.updatedAt) : now;
    call.duration.textContent = `用时 ${progressDuration(end - Date.parse(call.startedAt))}`;
    const attempt = call.attempts.get(call.data.attempt);
    attempt.details.classList.toggle("is-active", active);
    attempt.line.classList.toggle("is-active", active);
    if (phase !== call.data.phase) attempt.status.textContent = MODEL_PHASES[phase];
    else if (active) attempt.status.textContent = `${MODEL_PHASES[phase]} · ${progressDuration(now - Date.parse(call.data.attempt_started_at || call.startedAt))}`;
    const summary = groups.get(call.parent) || {done: 0, total: 0, active: []};
    summary.total += 1;
    if (phase === "success") summary.done += 1;
    if (active) summary.active.push(`${MODEL_ROLES[call.data.role] || "模型调用"}：${MODEL_PHASES[phase]}`);
    groups.set(call.parent, summary);
  }
  for (const [parent, group] of groups) {
    parent.querySelector(".model-stage-summary").textContent =
      `${group.done}/${group.total} 项完成${group.active.length ? ` · ${group.active.join("；")}` : ""}`;
  }
}
