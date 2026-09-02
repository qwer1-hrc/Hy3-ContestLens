const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {node} = require("./dom-stub.cjs");

const script = fs.readFileSync(path.join(__dirname, "../../src/hy3_contestlens/web/static/reports.js"), "utf8");
const flush = () => new Promise(setImmediate);

function setup() {
  const calls = [];
  const listeners = {};
  const checkboxes = [
    node({value: "run_a", checked: false, dataset: {terminal: "true", report: "true", hidden: "false"}}),
    node({value: "run_b", checked: false, dataset: {terminal: "false", report: "false", hidden: "false"}}),
  ];
  checkboxes.forEach((item, index) => { item.addEventListener = (event, callback) => { listeners[`check-${index}-${event}`] = callback; }; });
  const selectAll = node({checked: false, indeterminate: false, addEventListener: (event, callback) => { listeners[`all-${event}`] = callback; }});
  const buttons = {
    hide: node({dataset: {bulkAction: "hide"}}), show: node({dataset: {bulkAction: "show"}}),
    delete: node({dataset: {bulkAction: "delete"}}), html: node({dataset: {bulkExport: "html"}}), md: node({dataset: {bulkExport: "md"}}),
  };
  Object.entries(buttons).forEach(([name, button]) => { button.addEventListener = (event, callback) => { listeners[`${name}-${event}`] = callback; }; });
  const bulk = node({hidden: true, dataset: {actionToken: "token"},
    querySelector: (selector) => selector.includes('delete') ? buttons.delete : selector.includes('hide') ? buttons.hide : selector.includes('show') ? buttons.show : null,
    querySelectorAll: (selector) => selector === "button" ? Object.values(buttons) : selector.includes("bulk-action") ? [buttons.hide, buttons.show, buttons.delete] : [buttons.html, buttons.md],
  });
  const panel = node();
  const multi = node({addEventListener: (event, callback) => { listeners[`multi-${event}`] = callback; }});
  const count = node(); const status = node(); const control = node({dataset: {enabled: "false"}});
  const elements = {"bulk-report-actions": bulk, "reports-panel": panel, "toggle-multi-select": multi, "select-all-runs": selectAll, "bulk-selected-count": count, "bulk-action-status": status, "report-translation-control": control};
  let reloads = 0; let confirmed = true;
  vm.runInNewContext(script, {
    document: {body: {append: () => {}}, getElementById: (id) => elements[id], querySelectorAll: (selector) => selector === ".run-select" ? checkboxes : [] , addEventListener: () => {}},
    window: {visualViewport: {addEventListener: () => {}}, addEventListener: () => {}, innerWidth: 1000, innerHeight: 700},
    location: {reload: () => { reloads += 1; }}, confirm: () => confirmed,
    api: async (url, options) => { calls.push({url, options}); return {ok: true}; },
    fetch: async () => { throw new Error("unused"); }, URL: {}, setTimeout, clearTimeout,
  });
  return {calls, listeners, checkboxes, selectAll, buttons, bulk, panel, multi, count, status, reloads: () => reloads, setConfirmed: (value) => { confirmed = value; }};
}

test("selection updates count, select-all and safety states", () => {
  const ui = setup();
  assert.equal(ui.panel.classList.contains("selection-mode"), false);
  assert.equal(ui.multi.getAttribute("aria-pressed"), "false");
  ui.listeners["multi-click"]();
  assert.equal(ui.panel.classList.contains("selection-mode"), true);
  assert.equal(ui.multi.getAttribute("aria-pressed"), "true");
  ui.checkboxes[0].checked = true; ui.listeners["check-0-change"]();
  assert.equal(ui.bulk.hidden, false); assert.equal(ui.count.textContent, "1");
  assert.equal(ui.buttons.delete.disabled, false); assert.equal(ui.buttons.html.disabled, false);
  ui.checkboxes[1].checked = true; ui.listeners["check-1-change"]();
  assert.equal(ui.selectAll.checked, true); assert.equal(ui.buttons.delete.disabled, true);
  ui.listeners["multi-click"]();
  assert.equal(ui.bulk.hidden, true); assert.ok(ui.checkboxes.every((item) => !item.checked));
  assert.equal(ui.panel.classList.contains("selection-mode"), false);
});

test("bulk mutation sends one request and deletion requires confirmation", async () => {
  const ui = setup();
  ui.listeners["multi-click"]();
  ui.checkboxes[0].checked = true; ui.listeners["check-0-change"]();
  await ui.listeners["hide-click"](); await flush();
  assert.equal(ui.calls.length, 1);
  assert.deepEqual(JSON.parse(ui.calls[0].options.body), {run_ids: ["run_a"], action: "hide", action_token: "token", confirmation: null});
  assert.equal(ui.reloads(), 1);
  ui.setConfirmed(false); await ui.listeners["delete-click"]();
  assert.equal(ui.calls.length, 1);
  ui.setConfirmed(true); await ui.listeners["delete-click"](); await flush();
  assert.equal(JSON.parse(ui.calls[1].options.body).confirmation, "permanent");
});
