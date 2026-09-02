const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {node} = require("./dom-stub.cjs");

const script = fs.readFileSync(path.join(__dirname, "../../src/hy3_contestlens/web/static/reports.js"), "utf8");
const flush = () => new Promise(setImmediate);

function setup(dataset, snapshots) {
  const requests = [];
  const timers = [];
  const listeners = {};
  const retry = node({hidden: true, addEventListener: (event, callback) => { listeners.retry = callback; }});
  const message = {textContent: ""};
  const badge = node({dataset: {translationRunId: "run_a"}});
  const control = node({dataset: {enabled: "true", ...dataset}});
  const spinner = node();
  const actionListeners = [{}, {}];
  const menus = [0, 1].map((index) => node({
    id: `menu-${index}`, style: {}, offsetWidth: 220, offsetHeight: 180, open: false,
    addEventListener: (event, callback) => { actionListeners[index][`menu-${event}`] = callback; },
    matches: function () { return this.open; },
    hidePopover: function () { this.open = false; actionListeners[index]["menu-toggle"]?.({newState: "closed"}); },
  }));
  const actionButtons = [0, 1].map((index) => node({
    menuId: `menu-${index}`,
    addEventListener: (event, callback) => { actionListeners[index][`button-${event}`] = callback; },
    getAttribute: function (name) { return name === "popovertarget" ? this.menuId : this.attributes[name]; },
    getBoundingClientRect: () => ({left: 400, right: 436, top: 200, bottom: 236}),
  }));
  let reloads = 0;
  const elements = {
    "report-translation-control": control,
    "translation-queue-message": message,
    "retry-report-translation": retry,
    "report-activity-spinner": spinner,
    "menu-0": menus[0], "menu-1": menus[1],
  };
  vm.runInNewContext(script, {
    document: {getElementById: (id) => elements[id], querySelectorAll: (selector) => selector === ".run-action-trigger" ? actionButtons : [badge]},
    window: {innerWidth: 1000, innerHeight: 700, addEventListener: (event, callback) => { listeners[event] = callback; }},
    location: {reload: () => { reloads += 1; }},
    setTimeout: (callback) => { timers.push(callback); return timers.length; },
    clearTimeout: () => {},
    requestAnimationFrame: (callback) => callback(),
    api: async (url, options) => {
      requests.push({url, options});
      const response = snapshots[Math.min(requests.length - 1, snapshots.length - 1)];
      if (response instanceof Error) throw response;
      return response;
    },
  });
  return {requests, timers, listeners, retry, message, badge, control, spinner, actionButtons, actionListeners, menus, reloads: () => reloads};
}

function snapshot(status, busy = false) {
  return {busy, active_run_id: busy ? "run_a" : null, runs: [{run_id: "run_a", status, label: status, position: null, completed_segments: 0, total_segments: 2}]};
}

test("a current-run report submits priority once and polling only uses GET", async () => {
  const ui = setup({runId: "run_a", priority: "true"}, [snapshot("TRANSLATING", true), snapshot("READY")]);
  assert.equal(ui.spinner.hidden, false);
  assert.equal(ui.retry.classList.contains("is-loading"), true);
  await flush();
  assert.equal(ui.control.getAttribute("aria-busy"), "true");
  assert.equal(ui.badge.getAttribute("aria-busy"), "true");
  assert.equal(ui.badge.textContent, "格式化中");
  assert.equal(ui.requests[0].options.method, "POST");
  assert.deepEqual(JSON.parse(ui.requests[0].options.body), {priority_run_id: "run_a"});
  await ui.timers[0]();
  await flush();
  assert.equal(ui.requests.length, 2);
  assert.equal(ui.requests[1].options, undefined);
  assert.equal(ui.reloads(), 1);
  assert.equal(ui.spinner.hidden, true);
  assert.equal(ui.control.getAttribute("aria-busy"), "false");
  assert.equal(ui.badge.getAttribute("aria-busy"), "false");
});

test("report list starts a normal ordered batch without a priority override", async () => {
  const ui = setup({reportMode: "list"}, [snapshot("READY")]);
  await flush();
  assert.deepEqual(JSON.parse(ui.requests[0].options.body), {});
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.timers.length, 0);
  assert.equal(ui.spinner.hidden, true);
});

test("already-rendered cache does not reload or retry", async () => {
  const ui = setup({runId: "run_a", ready: "true"}, [snapshot("READY")]);
  await flush();
  assert.equal(ui.reloads(), 0);
  assert.equal(ui.timers.length, 0);
  assert.equal(ui.spinner.hidden, true);
  assert.equal(ui.badge.textContent, "已格式化");
});

test("failure only retries after the user presses the retry button", async () => {
  const ui = setup({runId: "run_a"}, [snapshot("FAILED"), snapshot("READY")]);
  await flush();
  assert.equal(ui.retry.hidden, false);
  assert.equal(ui.spinner.hidden, true);
  assert.equal(ui.badge.getAttribute("aria-busy"), "false");
  assert.equal(ui.timers.length, 0);
  assert.equal(ui.requests.length, 1);
  await ui.listeners.retry();
  assert.deepEqual(JSON.parse(ui.requests[1].options.body), {retry_run_id: "run_a", priority_run_id: "run_a"});
});

test("empty reports never submit and leaving the page stops polling", async () => {
  const empty = setup({enabled: "false"}, []);
  await flush();
  assert.equal(empty.requests.length, 0);
  const active = setup({runId: "run_a"}, [snapshot("TRANSLATING", true)]);
  await flush();
  active.listeners.pagehide();
  await active.timers[0]();
  assert.equal(active.requests.length, 1);
});

test("list activity stops once the queue is finished", async () => {
  const ui = setup({reportMode: "list"}, [snapshot("TRANSLATING", true), snapshot("READY")]);
  await flush();
  assert.equal(ui.spinner.hidden, false);
  assert.match(ui.message.textContent, /正在格式化/);
  await ui.timers[0]();
  assert.equal(ui.spinner.hidden, true);
  assert.match(ui.message.textContent, /已完成 1 份/);
});

test("a failed report does not spin while other reports are still processing", async () => {
  const ui = setup({runId: "run_a"}, [snapshot("FAILED", true)]);
  await flush();
  assert.equal(ui.spinner.hidden, true);
  assert.equal(ui.badge.getAttribute("aria-busy"), "false");
  assert.equal(ui.message.textContent, "格式化失败，请重试");
});

test("connection errors stop loading indicators", async () => {
  const ui = setup({runId: "run_a"}, [snapshot("TRANSLATING", true), new Error("offline")]);
  await flush();
  await ui.timers[0]();
  assert.equal(ui.spinner.hidden, true);
  assert.equal(ui.control.getAttribute("aria-busy"), "false");
  assert.equal(ui.badge.getAttribute("aria-busy"), "false");
  assert.match(ui.message.textContent, /状态同步失败/);
});

test("returning from browser history resumes GET polling without another POST", async () => {
  const ui = setup({runId: "run_a", ready: "true"}, [snapshot("READY"), snapshot("READY")]);
  await flush();
  ui.listeners.pagehide();
  ui.listeners.pageshow({persisted: true});
  await flush();
  assert.equal(ui.requests.length, 2);
  assert.equal(ui.requests[1].options, undefined);
});

test("opening another floating action card closes the previous one", async () => {
  const ui = setup({reportMode: "list"}, [snapshot("READY")]);
  await flush();
  ui.actionListeners[0]["button-click"]();
  ui.menus[0].open = true;
  ui.actionListeners[0]["menu-toggle"]({newState: "open"});
  assert.equal(ui.actionButtons[0].getAttribute("aria-expanded"), "true");
  assert.equal(ui.menus[0].style.left, "216px");
  ui.actionListeners[1]["button-click"]();
  assert.equal(ui.menus[0].open, false);
  ui.menus[1].open = true;
  ui.actionListeners[1]["menu-toggle"]({newState: "open"});
  assert.equal(ui.menus.filter((menu) => menu.open).length, 1);
  assert.equal(ui.actionButtons[0].getAttribute("aria-expanded"), "false");
  assert.equal(ui.actionButtons[1].getAttribute("aria-expanded"), "true");
});
