const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {resolve} = require('node:path');
const vm = require('node:vm');

test('history refresh replaces a stale running stage with its completed view', async () => {
  const history = {innerHTML: '', addEventListener() {}};
  const timers = new Map();
  const document = {
    getElementById(id) { return id === 'wf-task-history' ? history : null; },
    querySelectorAll() { return []; },
    dispatchEvent() {}
  };
  const sessionStorage = {getItem() { return null; }, setItem() {}, removeItem() {}};
  const context = {
    window: {}, document, sessionStorage,
    CustomEvent: function (type, options) { this.type = type; this.detail = options.detail; },
    setInterval(callback, delay) { timers.set(delay, callback); return delay; },
    clearInterval() {},
  };
  vm.runInNewContext(
    readFileSync(resolve(__dirname, '../static/js/workflow/commander-workflow.js'), 'utf8'),
    context
  );
  let completed = false;
  let fetchCount = 0;
  const api = {
    getBackendHealth: async () => ({status: 'ok'}),
    loadRun: async () => ({
      workflow_ids: ['wf-orient'],
      submissions: [{workflow_id: 'wf-orient', snapshot: {
        stage_transfer: {phase: 'TRACK', checkpoint_id: 'MAR-CP-ASSESS', submission_ordinal: 2}
      }}],
      workflow_views: []
    }),
    getWorkflowView: async () => {
      fetchCount += 1;
      return {
        workflow_id: 'wf-orient', status: completed ? 'completed' : 'running',
        terminal: completed, orchestration: {counts: {total: 2}},
      };
    }
  };
  context.window.PlatformWorkflow.init(api);
  context.window.PlatformWorkflow.syncRun('run-1');
  await new Promise(setImmediate);
  assert.match(history.innerHTML, /航迹评估[\s\S]*执行中/);

  completed = true;
  assert.equal(typeof timers.get(5000), 'function');
  timers.get(5000)();
  await new Promise(setImmediate);
  assert.match(history.innerHTML, /航迹评估[\s\S]*已完成/);
  assert.equal(fetchCount, 2);
});
