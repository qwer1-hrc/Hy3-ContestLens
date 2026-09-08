const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const {node} = require('./dom-stub.cjs');

function element(tag = 'div') {
  const e = node({tag, children: [], className: '', open: false});
  e.append = (...children) => e.children.push(...children);
  e.querySelector = (selector) => {
    for (const child of e.children) {
      if (child.className.split(' ').includes(selector.slice(1))) return child;
      const nested = child.querySelector(selector);
      if (nested) return nested;
    }
    return null;
  };
  return e;
}

function setup() {
  const parents = new Map([7, 12].map(seq => {
    const parent = element();
    const main = element(); main.className = 'event-main';
    const body = element(); body.className = 'event-body';
    parent.append(main, body);
    return [seq, parent];
  }));
  const context = {document: {
    createElement: element,
    querySelector: selector => parents.get(Number(selector.match(/"(\d+)"/)[1])),
  }};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../../src/hy3_contestlens/web/static/model-progress.js'), 'utf8'), context);
  let seq = 10;
  const emit = (id, phase, attempt = 1, parent = 7, extra = {}) => {
    const event = {seq: seq++, created_at: new Date().toISOString(), data: {
      parent_seq: parent, call_id: id, phase, role: id === 'a' ? 'algorithm_critic' : 'code_critic',
      attempt, max_attempts: 3, started_at: new Date(Date.now() - 60000).toISOString(), elapsed_ms: 1000, ...extra,
    }};
    context.appendModelProgress(event);
    return event;
  };
  const call = id => vm.runInContext(`modelProgressCalls.get('${id}')`, context);
  return {context, parents, emit, call};
}

test('parallel calls nest under their stage and preserve manual collapse during streaming', () => {
  const ui = setup();
  ui.emit('a', 'reasoning'); ui.emit('b', 'waiting');
  const a = ui.call('a'); a.details.open = false;
  ui.emit('a', 'generating'); ui.emit('a', 'generating');
  assert.equal(a.details.open, false);
  assert.equal(a.attempts.get(1).body.children.length, 2);
  ui.emit('a', 'success');
  ui.context.updateModelProgressState('REVIEWING');
  assert.equal(a.details.classList.contains('is-active'), false);
  assert.equal(ui.call('b').details.classList.contains('is-active'), true);
  assert.match(ui.parents.get(7).querySelector('.model-stage-summary').textContent, /1\/2 项完成/);
});

test('retry history, duplicate events and a later review stage stay independent', () => {
  const ui = setup();
  ui.emit('a', 'retrying', 1, 7, {failure_kind: 'repetitive_output', retry_delay_seconds: 2});
  ui.context.updateModelProgressState('REVIEWING');
  const event = ui.emit('a', 'waiting', 2);
  ui.context.appendModelProgress(event);
  ui.emit('later', 'reasoning', 1, 12);
  assert.equal(ui.call('a').attempts.size, 2);
  assert.equal(ui.call('a').attempts.get(2).body.children.length, 1);
  assert.equal(ui.call('a').attempts.get(1).line.classList.contains('is-active'), false);
  assert.match(ui.call('a').attempts.get(1).text.textContent, /提前停止无效生成/);
  assert.equal(ui.call('later').parent, ui.parents.get(12));
});

test('terminal state or lost connection stops nested animations without claiming success', () => {
  for (const state of ['CANCELLED', 'FAILED', 'COMPLETED', 'DISCONNECTED', 'INTERRUPTED']) {
    const ui = setup(); ui.emit('a', 'reasoning');
    ui.context.updateModelProgressState('REVIEWING');
    ui.context.updateModelProgressState(state);
    assert.equal(ui.call('a').details.classList.contains('is-active'), false);
    assert.equal(ui.call('a').attempts.get(1).line.classList.contains('is-active'), false);
    assert.notEqual(ui.call('a').status.textContent, '已完成');
  }
});
