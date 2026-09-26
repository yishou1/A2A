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

test('coastal Engage and Assess activities share one execution timeline', async () => {
  const elements = new Map();
  const timers = new Map();
  const element = id => {
    if (!elements.has(id)) {
      elements.set(id, {
        innerHTML: '', textContent: '', className: '', hidden: false, checked: true,
        style: {}, dataset: {},
        classList: {add() {}, toggle() {}},
        addEventListener() {}, setAttribute() {},
      });
    }
    return elements.get(id);
  };
  const document = {
    getElementById: element,
    querySelectorAll() { return []; },
    dispatchEvent() {},
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

  const submissions = [
    {workflow_id: 'wf-engage', snapshot: {run_id: 'run-cjr', stage_transfer: {
      phase: 'ENGAGE', checkpoint_id: 'CJR-CP-ENGAGE', submission_ordinal: 5,
    }}},
    {workflow_id: 'wf-assess', snapshot: {run_id: 'run-cjr', stage_transfer: {
      phase: 'ASSESS', checkpoint_id: 'CJR-CP-CLOSE', submission_ordinal: 6,
    }}},
  ];
  const views = {
    'wf-engage': {
      workflow_id: 'wf-engage', status: 'completed', terminal: true, progress_pct: 100,
      run: {run_id: 'run-cjr', current: true}, submission: submissions[0].snapshot,
      orchestration: {counts: {total: 1, completed: 1}, activities: [
        {activity_id: 'activatity-002-executionsimulation', role: 'simulation_execution', status: 'completed', index: 2},
      ], trace: []},
      activity_details: {'activatity-002-executionsimulation': {activity_id: 'activatity-002-executionsimulation'}},
    },
    'wf-assess': {
      workflow_id: 'wf-assess', status: 'completed', terminal: true, progress_pct: 100,
      run: {run_id: 'run-cjr', current: true}, submission: submissions[1].snapshot,
      orchestration: {counts: {total: 1, completed: 1}, activities: [
        {activity_id: 'activatity-002-effectevaluation', role: 'closed_loop', status: 'completed', index: 2},
      ], trace: []},
      activity_details: {'activatity-002-effectevaluation': {activity_id: 'activatity-002-effectevaluation'}},
    },
  };
  const api = {
    getBackendHealth: async () => ({status: 'ok'}),
    loadRun: async () => ({workflow_ids: ['wf-engage', 'wf-assess'], submissions, workflow_views: []}),
    getWorkflowView: async id => views[id],
  };

  context.window.PlatformWorkflow.init(api);
  context.window.PlatformWorkflow.syncRun('run-cjr');
  context.window.PlatformWorkflow.track('wf-assess', 'run-cjr');
  for (let i = 0; i < 5; i += 1) await new Promise(setImmediate);

  const timeline = element('wf-activity-list').innerHTML;
  assert.match(timeline, /simulation_execution/);
  assert.match(timeline, /closed_loop/);
  assert.equal(element('wf-activity-count').textContent, '2 项');
});
