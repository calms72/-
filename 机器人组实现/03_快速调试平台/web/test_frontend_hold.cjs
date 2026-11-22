'use strict';
// Offline UI regression: every request is mocked; no browser or hardware is used.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const elements = new Map();
class Element {
  constructor(tag = 'div') {
    this.tagName = tag;
    this.attributes = {};
    this.children = [];
    this.listeners = {};
    this.value = '';
    this.textContent = '';
    this.style = {};
    this.classes = new Set();
    this.classList = {
      add: name => this.classes.add(name),
      remove: name => this.classes.delete(name),
      toggle: (name, enabled) => enabled ? this.classes.add(name) : this.classes.delete(name),
    };
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === 'id') elements.set(String(value), this);
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(name, listener) { (this.listeners[name] ||= []).push(listener); }
  async emit(name) { await Promise.all((this.listeners[name] || []).map(listener => listener({preventDefault() {}}))); }
  querySelector(tag) { return this.children.find(child => child.tagName === tag); }
  get firstElementChild() { return this.children[0]; }
  reportValidity() { return true; }
}

const template = fs.readFileSync(path.join(__dirname, 'templates/index.html'), 'utf8');
for (const [, id] of template.matchAll(/\bid="([^"]+)"/g)) {
  const node = new Element();
  node.setAttribute('id', id);
}
elements.get('connection').append(new Element('span'));
elements.get('job-progress').append(new Element('span'));
elements.get('duration').value = '1600';
const document = {
  activeElement: null,
  getElementById: id => {
    assert.ok(elements.has(id), `Unknown DOM element: ${id}`);
    return elements.get(id);
  },
  createElement: tag => new Element(tag),
  createTextNode: text => ({textContent: text}),
  querySelector: selector => {
    assert.equal(selector, 'meta[name="control-token"]');
    return {content: 'offline-test-token'};
  },
  querySelectorAll: selector => {
    const attribute = /^\[([^\]]+)\]$/.exec(selector)?.[1];
    assert.ok(attribute, `Unsupported test selector: ${selector}`);
    return [...elements.values()].filter(node => attribute in node.attributes);
  },
};

const clone = value => JSON.parse(JSON.stringify(value));
const limits = [2000, 4095, 4095, 4095, 4095, 4095];
function fixture(overrides = {}) {
  return {
    connected: true, simulate: false, busy: false, holding: false, port: 'TEST-COM',
    confirmed_at: '2026-11-11T10:00:00+08:00', message: '动作完成',
    positions: [0, 600, 0, 0, 0, 0], desired_positions: [1000, 612, 0, 0, 0, 0],
    joints: limits.map((limit, index) => ({
      id: index + 1, name: `关节${index + 1}`, limit, zero_raw: 0,
      end_raw: limit, end_angle: 90, calibrated: false,
    })),
    gestures: [], recordings: [], recent: [], recording: null, job: null,
    ...overrides,
  };
}
let serverState = fixture();
let getState = async () => clone(serverState);
const posts = [];
let post = async (route, body) => {
  posts.push({route, body});
  return {ok: true, value: {job_id: 'offline-job'}};
};
const context = vm.createContext({
  document, console,
  setTimeout: () => 1, clearTimeout() {}, setInterval() {},
  fetch: async (url, options) => {
    assert.ok(url.startsWith('/api/'), 'Only mocked API paths are allowed');
    if (url === '/api/state') return {ok: true, json: async () => clone(await getState())};
    assert.equal(options.method, 'POST');
    assert.equal(options.headers['X-Control-Token'], 'offline-test-token');
    const response = await post(url.slice(5), JSON.parse(options.body));
    return {ok: response.ok, json: async () => response.value};
  },
});
vm.runInContext(fs.readFileSync(path.join(__dirname, 'static/app.js'), 'utf8'), context);
const ui = id => elements.get(id);
const tick = () => new Promise(resolve => setImmediate(resolve));

async function main() {
  await context.refresh();
  assert.equal(ui('angle-1').value, '45.0', 'Initial input shows requested target');
  assert.equal(ui('slider-1').value, '45.0');
  assert.equal(ui('raw-1').textContent, 0, 'Feedback stays separate from requested target');

  ui('angle-1').value = '63';
  await ui('angle-1').emit('input');
  let resolveOldPoll;
  let reads = 0;
  const oldState = clone(serverState);
  const oldPollResult = new Promise(resolve => { resolveOldPoll = resolve; });
  getState = async () => ++reads === 1 ? oldPollResult : clone(serverState);
  const oldPoll = context.refresh();
  await tick();
  post = async (route, body) => {
    posts.push({route, body});
    serverState = fixture({
      desired_positions: [1400, 612, 0, 0, 0, 0], busy: true,
      job: {id: 'new', status: 'preparing', message: '正在准备', progress: 0},
    });
    return {ok: true, value: {job_id: 'new'}};
  };
  let applied = false;
  const applying = ui('apply-1').emit('click').then(() => { applied = true; });
  await tick();
  assert.equal(posts.at(-1).body.angle, 63);
  assert.equal(applied, false, 'Apply must await a fresh read after the POST');
  assert.equal(reads, 1, 'State polls must be serialized');
  resolveOldPoll(oldState);
  await oldPoll;
  await applying;
  assert.equal(reads, 2, 'A new state read follows the older poll');
  assert.equal(ui('angle-1').value, '63', 'Older feedback does not undo a submitted input');
  assert.equal(ui('slider-1').value, '63');
  assert.equal(ui('raw-1').textContent, 0);
  await context.refresh();
  assert.equal(ui('angle-1').value, '63.0', 'Later polls preserve the requested target');

  getState = async () => clone(serverState);
  serverState = fixture({
    desired_positions: [1400, 612, 0, 0, 0, 0], positions: [1377, 607, 0, 0, 0, 0],
    holding: true, job: {id: 'new', status: 'done'},
  });
  await context.refresh();
  assert.equal(ui('angle-1').value, '63.0');
  assert.equal(ui('raw-1').textContent, 1377);
  assert.equal(ui('status-text').textContent, '已应用，正在保持；点击自然张开或停止并张开可释放');

  ui('angle-1').value = '72';
  await ui('angle-1').emit('input');
  post = async () => ({ok: false, value: {error: '离线拒绝示例'}});
  await ui('apply-1').emit('click');
  await context.refresh();
  assert.equal(ui('angle-1').value, '72', 'Rejected input remains available for correction');
  assert.equal(ui('alert').textContent, '离线拒绝示例');

  post = async (route, body) => {
    posts.push({route, body});
    serverState = fixture({desired_positions: [0, 612, 0, 0, 0, 0]});
    return {ok: true, value: {job_id: 'cycle'}};
  };
  await ui('cycle-btn').emit('click');
  assert.deepEqual(posts.at(-1), {route: 'gesture', body: {id: 'grasp-cycle'}});
  await context.refresh();
  assert.equal(ui('angle-1').value, '0.0', 'Explicit grasp/open cycle still adopts its open target');

  serverState = fixture({holding: true, job: {id: 'failed', status: 'error', message: '保持异常'}, message: '保持异常'});
  await context.refresh();
  assert.equal(ui('status-text').textContent, '保持异常', 'Holding notice must not hide an error');
  serverState = fixture({holding: true, connected: false, message: '连接已断开'});
  await context.refresh();
  assert.equal(ui('status-text').textContent, '连接已断开');
  serverState = fixture({holding: true, simulate: true, message: '模拟动作完成'});
  await context.refresh();
  assert.equal(ui('status-text').textContent, '模拟动作完成');

  serverState = fixture({positions: [400, 612, 0, 0, 0, 0]});
  delete serverState.desired_positions;
  await context.refresh();
  assert.equal(ui('angle-1').value, '18.0', 'Older state responses remain readable');
  assert.ok(template.includes('应用后持续保持，直到下次操作或点击张开。'));
  assert.match(template, /id="cycle-btn"[^>]*title="[^"]*2 秒[^\"]*自动张开/);
  console.log('PASS: target/feedback separation, serialized refresh race, holding notice, rejection, explicit cycle and legacy state');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
