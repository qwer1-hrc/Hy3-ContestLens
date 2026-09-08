const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {node} = require("./dom-stub.cjs");

const script = fs.readFileSync(path.join(__dirname, "../../src/hy3_contestlens/web/static/app.js"), "utf8");
const flush = () => new Promise(setImmediate);

function setup(types = [], fetch = async () => { throw new Error("offline"); }) {
  const rows = types.map((eventType) => {
    const tag = node({hidden: true});
    return node({dataset: {eventType}, tag, querySelector: () => tag});
  });
  const elements = {
    "event-list": {querySelectorAll: () => rows},
    "workflow-current": node(), "run-activity-spinner": node(),
    "run-status": node(), "current-work": node(), "run-result": node(),
    "run-create-button": node(), "run-create-result": node(), "run-problem": node({value: "road"}),
    "repair-enabled": node({checked: true}), "repair-rounds": node({value: "3"}),
  };
  const destinations = [];
  const context = {
    document: {getElementById: (id) => elements[id]}, fetch,
    crypto: {randomUUID: () => "test-id"}, location: {assign: (url) => destinations.push(url)},
    setTimeout, alert: () => {},
  };
  vm.createContext(context);
  vm.runInContext(script, context);
  return {context, elements, rows, destinations};
}

test("each workflow stage animates only its latest matching event", () => {
  const stages = ["ANALYZING", "SOLVING", "REVIEWING", "COMPILING", "JUDGING", "LOCALIZING", "REPAIRING", "REJUDGING"];
  const ui = setup(["CREATED", ...stages, "COMPILE_COMPLETED", "JUDGE_COMPLETED"]);
  for (const stage of stages) {
    ui.context.updateWorkflowEventState(stage);
    assert.equal(ui.elements["run-activity-spinner"].hidden, false);
    assert.equal(ui.elements["workflow-current"].getAttribute("aria-busy"), "true");
    for (const row of ui.rows) {
      const active = row.dataset.eventType === stage;
      assert.equal(row.classList.contains("is-current"), active);
      assert.equal(row.getAttribute("aria-busy"), String(active));
      assert.equal(row.tag.hidden, !active);
    }
  }
  const repeated = setup(["REVIEWING", "COMPILING", "REVIEWING"]);
  repeated.context.updateWorkflowEventState("REVIEWING");
  assert.equal(repeated.rows[0].tag.hidden, true);
  assert.equal(repeated.rows[2].tag.hidden, false);
});

test("completion, failure and cancellation stop all workflow animations", () => {
  for (const status of ["COMPLETED", "FAILED", "CANCELLED"]) {
    const ui = setup(["SOLVING", "JUDGING"]);
    ui.context.updateWorkflowEventState("JUDGING");
    ui.context.updateWorkflowEventState(status);
    assert.equal(ui.elements["run-activity-spinner"].hidden, true);
    assert.equal(ui.elements["workflow-current"].getAttribute("aria-busy"), "false");
    assert.ok(ui.rows.every((row) => !row.classList.contains("is-current") && row.tag.hidden));
  }
});

test("an interrupted run is shown as recoverable active work", () => {
  const ui = setup(["INTERRUPTED"]);
  ui.context.updateRunSummary({status: "INTERRUPTED", result: null});
  ui.context.updateWorkflowEventState("INTERRUPTED");
  assert.match(ui.elements["run-status"].textContent, /中断/);
  assert.match(ui.elements["current-work"].textContent, /检查点/);
  assert.equal(ui.elements["run-activity-spinner"].hidden, false);
  assert.equal(ui.rows[0].tag.textContent, "等待中");
});

test("a polling failure clears running indicators", async () => {
  const ui = setup(["SOLVING"]);
  ui.context.updateWorkflowEventState("SOLVING");
  await ui.context.watchRun("run_a");
  assert.equal(ui.elements["run-activity-spinner"].hidden, true);
  assert.equal(ui.rows[0].tag.hidden, true);
  assert.match(ui.elements["current-work"].textContent, /无法继续同步/);
});

test("starting a run shows loading and rejects duplicate clicks until navigation", async () => {
  let resolveStart;
  let requests = 0;
  const ui = setup([], async () => {
    requests += 1;
    if (requests === 1) return {ok: true, json: async () => ({run_id: "run_a"})};
    return new Promise((resolve) => { resolveStart = resolve; });
  });
  const running = ui.context.createRun();
  await flush();
  const button = ui.elements["run-create-button"];
  assert.equal(button.classList.contains("is-loading"), true);
  assert.equal(button.getAttribute("aria-busy"), "true");
  await ui.context.createRun();
  assert.equal(requests, 2);
  resolveStart({ok: true, json: async () => ({status: "QUEUED"})});
  await running;
  assert.deepEqual(ui.destinations, ["/ui/runs/run_a"]);
  assert.equal(button.classList.contains("is-loading"), false);
});

test("a start error stops loading and re-enables the button", async () => {
  const ui = setup();
  await ui.context.createRun();
  const button = ui.elements["run-create-button"];
  assert.equal(button.disabled, false);
  assert.equal(button.getAttribute("aria-busy"), "false");
  assert.equal(button.classList.contains("is-loading"), false);
});

test("WebUI opts into asking about images without changing API defaults", async () => {
  const requests = [];
  const ui = setup([], async (path, options) => {
    requests.push({path, body: options.body && JSON.parse(options.body)});
    return {ok: true, json: async () => ({run_id: "run_a"})};
  });
  await ui.context.createRun();
  assert.equal(requests[0].body.image_understanding, "ask");
});

test("waiting for an image decision is highlighted without a running spinner", () => {
  const ui = setup(["ANALYZING", "WAITING_FOR_IMAGE_CONFIRMATION"]);
  ui.context.updateWorkflowEventState("WAITING_FOR_IMAGE_CONFIRMATION");
  assert.equal(ui.elements["run-activity-spinner"].hidden, true);
  assert.equal(ui.rows[1].classList.contains("is-current"), true);
  assert.equal(ui.rows[1].tag.textContent, "等待选择");
});

test("image choice sends once and refreshed state disables stale controls", async () => {
  let finish;
  const requests = [];
  const ui = setup([], async (path, options) => {
    requests.push({path, body: JSON.parse(options.body)});
    return new Promise((resolve) => { finish = resolve; });
  });
  const panel = {buttons: [node(), node()], message: node(), pending: false};
  ui.context.panel = panel;
  vm.runInContext('imageChoicePanels.set("choice_1", panel)', ui.context);
  const choosing = ui.context.chooseImageUnderstanding("run_a", "choice_1", "skip");
  await flush();
  await ui.context.chooseImageUnderstanding("run_a", "choice_1", "use");
  assert.equal(requests.length, 1);
  assert.deepEqual(requests[0], {path: "/api/v1/runs/run_a/image-understanding", body: {request_id: "choice_1", choice: "skip"}});
  finish({ok: true, json: async () => ({choice: "skip"})});
  await choosing;
  ui.context.syncImageChoices({status: "ANALYZING", image_understanding: {request_id: "choice_1", choice: "skip"}});
  assert.ok(panel.buttons.every((button) => button.disabled));
  assert.match(panel.message.textContent, /已跳过/);
});

test("image choice request failure permits retry and cancellation disables it", async () => {
  const ui = setup();
  const panel = {buttons: [node(), node()], message: node(), pending: false};
  ui.context.panel = panel;
  vm.runInContext('imageChoicePanels.set("choice_1", panel)', ui.context);
  await ui.context.chooseImageUnderstanding("run_a", "choice_1", "use");
  assert.ok(panel.buttons.every((button) => !button.disabled));
  assert.match(panel.message.textContent, /选择未提交/);
  ui.context.syncImageChoices({status: "CANCELLED", image_understanding: {request_id: "choice_1", choice: null}});
  assert.ok(panel.buttons.every((button) => button.disabled));
});

test("image dependency warnings explain that no image was sent", () => {
  const ui = setup();
  assert.match(ui.context.imageWarningText({error_code: "IMAGE_DEPENDENCY_MISSING", missing: ["Pillow", "pypdfium2"]}), /Pillow、pypdfium2/);
  assert.match(ui.context.imageWarningText({label: "PDF 第 4 页", error_code: "IMAGE_MODEL_FAILED"}), /PDF 第 4 页.*模型/);
  assert.match(ui.context.imageWarningText({error_code: "IMAGE_MODEL_FAILED", failure_kind: "stream_incomplete", duration_ms: 32800, http_status: 200}), /提前结束.*HTTP 200.*32\.8 秒/);
  assert.match(ui.context.imageWarningText({error_code: "IMAGE_MODEL_FAILED", provider_error_type: "engine_overloaded_error", attempts: 3, http_status: 429}), /节点当前过载.*HTTP 429.*engine_overloaded_error.*尝试 3 次/);
  assert.match(ui.context.imageWarningText({label: "PDF 第 5 页", error_code: "IMAGE_SKIPPED_AFTER_RATE_LIMIT"}), /PDF 第 5 页.*未再发送/);
  assert.match(ui.context.imageWarningText({error_code: "IMAGE_MODEL_FAILED", type: "TimeoutError", http_status: 200}), /本地总时限.*不代表生成完成/);
  assert.match(ui.context.imageWarningText({error_code: "IMAGE_MODEL_FAILED", failure_kind: "transport_timeout"}), /等待后续数据超时/);
});
