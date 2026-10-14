const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {resolve} = require('node:path');
const vm = require('node:vm');

test('coastal tactical input and output previews stay fixed when workflow media changes', async () => {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      innerHTML: '', textContent: '', className: '', checked: true, style: {}, dataset: {},
      classList: {add() {}, toggle() {}}, addEventListener() {}, setAttribute() {},
    });
    return elements.get(id);
  };
  const context = {
    window: {}, document: {getElementById: element, querySelectorAll() { return []; }, dispatchEvent() {}},
    sessionStorage: {getItem() { return null; }, setItem() {}, removeItem() {}},
    CustomEvent: function () {}, setInterval() { return 1; }, clearInterval() {},
  };
  vm.runInNewContext(readFileSync(resolve(__dirname, '../static/js/workflow/commander-workflow.js'), 'utf8'), context);
  let stage = 0;
  const api = {
    getBackendHealth: async () => ({status: 'ok'}),
    getWorkflowView: async () => ({
      workflow_id: 'wf-' + stage, status: 'completed', terminal: true,
      submission: {scenario_id: 'coastal-joint-recon-strike', attachments: [{
        id: 'CJR-MEDIA-03-' + stage, name: 'EO changing frame', mime_type: 'image/png', uri: '/changing-input-' + stage + '.png',
      }]},
      orchestration: {counts: {total: 1, completed: 1}, activities: [{activity_id: 'tia', role: 'tactical_intelligence', status: 'completed'}]},
      activity_details: {tia: {role: 'tactical_intelligence', input_detail: {}, output_detail: {}, output_attachments: [{
        id: 'CJR-MEDIA-03-DET', name: 'EO annotated', mime_type: 'image/png', uri: '/changing-output-' + stage + '.png',
      }]}},
    }),
  };
  context.window.PlatformWorkflow.init(api, {getScenarioId: () => 'coastal-joint-recon-strike'});
  for (stage = 0; stage < 2; stage++) {
    context.window.PlatformWorkflow.track('wf-' + stage);
    await new Promise(setImmediate);
    for (const scope of ['input', 'output']) {
      assert.equal(context.window.PlatformWorkflow.locateActivity('tia', scope), true);
      const html = element('wf-activity-detail').innerHTML;
      const file = scope === 'input' ? 'coastal-eo-missile-site.png' : 'coastal-eo-missile-site-annotated.png';
      assert.match(html, new RegExp('/static/ui-previews/' + file));
      assert.doesNotMatch(html, /changing-(input|output)/);
    }
  }
});

test('carrier tactical input and output keep structured details without images', async () => {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      innerHTML: '', textContent: '', className: '', checked: true, style: {}, dataset: {},
      classList: {add() {}, toggle() {}}, addEventListener() {}, setAttribute() {},
    });
    return elements.get(id);
  };
  const context = {
    window: {}, document: {getElementById: element, querySelectorAll() { return []; }, dispatchEvent() {}},
    sessionStorage: {getItem() { return null; }, setItem() {}, removeItem() {}},
    CustomEvent: function () {}, setInterval() { return 1; }, clearInterval() {},
  };
  vm.runInNewContext(readFileSync(resolve(__dirname, '../static/js/workflow/commander-workflow.js'), 'utf8'), context);
  context.window.PlatformWorkflow.init({
    getBackendHealth: async () => ({status: 'ok'}),
    getWorkflowView: async () => ({
      workflow_id: 'wf-carrier', status: 'completed', terminal: true,
      submission: {scenario_id: 'air-space-sea-carrier-strike'},
      orchestration: {counts: {total: 1, completed: 1}, activities: [{activity_id: 'tia', role: 'tactical_intelligence', status: 'completed'}]},
      activity_details: {tia: {role: 'tactical_intelligence',
        input_detail: {__all__: {value: {input_marker: 'carrier-current-input'}}},
        output_detail: {__all__: {value: {output_marker: 'carrier-current-output'}}},
      }},
    }),
  }, {getScenarioId: () => 'air-space-sea-carrier-strike'});
  context.window.PlatformWorkflow.track('wf-carrier');
  await new Promise(setImmediate);
  for (const scope of ['input', 'output']) {
    assert.equal(context.window.PlatformWorkflow.locateActivity('tia', scope), true);
    const html = element('wf-activity-detail').innerHTML;
    assert.doesNotMatch(html, /<img|workflow-detection-preview/);
    assert.match(html, new RegExp('carrier-current-' + scope));
  }
});

test('automatic polls use brief status and fetch terminal details immediately', async () => {
  const timers = new Map();
  const context = {
    window: {},
    document: {getElementById() { return null; }, querySelectorAll() { return []; }, dispatchEvent() {}},
    sessionStorage: {getItem() { return 'wf-one'; }, setItem() {}, removeItem() {}},
    CustomEvent: function () {},
    setInterval(callback, delay) { timers.set(delay, callback); return delay; }, clearInterval() {},
  };
  vm.runInNewContext(readFileSync(resolve(__dirname, '../static/js/workflow/commander-workflow.js'), 'utf8'), context);
  let state = 'running', fullCount = 0, briefCount = 0;
  const api = {
    getBackendHealth: async () => ({status: 'ok'}),
    getWorkflowBrief: async () => { briefCount++; return {status: state}; },
    getWorkflowView: async () => {
      fullCount++;
      return {workflow_id: 'wf-one', status: state, terminal: state === 'completed', orchestration: {counts: {total: 1}}};
    },
  };
  context.window.PlatformWorkflow.init(api);
  await new Promise(setImmediate);
  assert.equal(fullCount, 1);
  timers.get(2000)();
  await new Promise(setImmediate);
  assert.equal(briefCount, 1);
  assert.equal(fullCount, 1);
  state = 'completed';
  timers.get(2000)();
  await new Promise(setImmediate);
  assert.equal(fullCount, 2);
  timers.get(2000)();
  await new Promise(setImmediate);
  assert.equal(fullCount, 2);
});

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
  assert.match(timeline, /executionsimulation/);
  assert.match(timeline, /effectevaluation/);
  assert.equal(element('wf-activity-count').textContent, '2 项');
});

test('carrier first-wave authorization workflow is grouped under execution and review', async () => {
  const elements = new Map();
  const timers = new Map();
  const element = id => {
    if (!elements.has(id)) {
      elements.set(id, {
        innerHTML: '', textContent: '', className: '', hidden: false, checked: true,
        style: {}, dataset: {}, classList: {add() {}, toggle() {}},
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
  const context = {
    window: {}, document,
    sessionStorage: {getItem() { return null; }, setItem() {}, removeItem() {}},
    CustomEvent: function (type, options) { this.type = type; this.detail = options.detail; },
    setInterval(callback, delay) { timers.set(delay, callback); return delay; },
    clearInterval() {},
  };
  vm.runInNewContext(
    readFileSync(resolve(__dirname, '../static/js/workflow/commander-workflow.js'), 'utf8'),
    context
  );
  const submission = {
    run_id: 'run-asc',
    stage_transfer: {phase: 'ACT', checkpoint_id: 'ASC-CP-WAVE1', submission_ordinal: 5},
  };
  context.window.PlatformWorkflow.init({
    getBackendHealth: async () => ({status: 'ok'}),
    loadRun: async () => ({
      workflow_ids: ['wf-wave1'],
      submissions: [{workflow_id: 'wf-wave1', snapshot: submission}],
      workflow_views: [],
    }),
    getWorkflowView: async () => ({
      workflow_id: 'wf-wave1', status: 'completed', terminal: true,
      submission, orchestration: {counts: {total: 1, completed: 1}, activities: [], trace: []},
      activity_details: {},
    }),
  });
  context.window.PlatformWorkflow.syncRun('run-asc');
  context.window.PlatformWorkflow.track('wf-wave1', 'run-asc');
  for (let i = 0; i < 5; i += 1) await new Promise(setImmediate);

  const taskHistory = element('wf-task-history').innerHTML;
  assert.match(taskHistory, /执行与复核/);
  assert.match(taskHistory, /ASC-CP-WAVE1/);
  assert.doesNotMatch(taskHistory, /第4阶段.*未生成任务/);
});

test('selecting a coastal history stage replaces the activity timeline', async () => {
  const elements = new Map();
  const timers = new Map();
  const element = id => {
    if (!elements.has(id)) {
      elements.set(id, {
        innerHTML: '', textContent: '', className: '', hidden: false, checked: true,
        style: {}, dataset: {}, listeners: {},
        classList: {add() {}, toggle() {}}, setAttribute() {},
        addEventListener(type, callback) { this.listeners[type] = callback; },
      });
    }
    return elements.get(id);
  };
  const document = {
    getElementById: element,
    querySelectorAll() { return []; },
    dispatchEvent() {},
  };
  const context = {
    window: {}, document,
    sessionStorage: {getItem() { return null; }, setItem() {}, removeItem() {}},
    CustomEvent: function (type, options) { this.type = type; this.detail = options.detail; },
    setInterval(callback, delay) { timers.set(delay, callback); return delay; },
    clearInterval() {},
  };
  vm.runInNewContext(
    readFileSync(resolve(__dirname, '../static/js/workflow/commander-workflow.js'), 'utf8'),
    context
  );
  const submissions = [
    {workflow_id: 'wf-cue', snapshot: {scenario_id: 'coastal-joint-recon-strike', run_id: 'run-cjr', stage_transfer: {
      phase: 'FIND', checkpoint_id: 'CJR-CP-CUE', submission_ordinal: 1,
    }}},
    {workflow_id: 'wf-plan', snapshot: {scenario_id: 'coastal-joint-recon-strike', run_id: 'run-cjr', stage_transfer: {
      phase: 'TARGET', checkpoint_id: 'CJR-CP-PLAN', submission_ordinal: 4,
    }}},
  ];
  const views = {
    'wf-cue': {
      workflow_id: 'wf-cue', status: 'completed', terminal: true, progress_pct: 100,
      submission: submissions[0].snapshot,
      orchestration: {counts: {total: 1, completed: 1}, activities: [
        {activity_id: 'cue-tia', role: 'tactical_intelligence', agent: 'agent-tia', status: 'completed', index: 1},
      ], trace: []}, activity_details: {},
    },
    'wf-plan': {
      workflow_id: 'wf-plan', status: 'completed', terminal: true, progress_pct: 100,
      submission: submissions[1].snapshot,
      orchestration: {counts: {total: 1, completed: 1}, activities: [
        {activity_id: 'plan-decision', role: 'decision_planning', agent: 'agent-plan', status: 'completed', index: 1},
      ], trace: []}, activity_details: {},
    },
  };
  const api = {
    getBackendHealth: async () => ({status: 'ok'}),
    loadRun: async () => ({workflow_ids: ['wf-cue', 'wf-plan'], submissions, workflow_views: []}),
    getWorkflowView: async id => views[id],
  };

  context.window.PlatformWorkflow.init(api, {getScenarioId: () => 'coastal-joint-recon-strike'});
  context.window.PlatformWorkflow.syncRun('run-cjr');
  context.window.PlatformWorkflow.track('wf-cue', 'run-cjr');
  for (let i = 0; i < 5; i += 1) await new Promise(setImmediate);
  assert.match(element('wf-activity-list').innerHTML, /agent-tia/);

  element('wf-task-history').listeners.click({
    target: {closest() { return {dataset: {workflowHistory: 'wf-plan'}}; }},
  });
  assert.match(element('wf-activity-list').innerHTML, /agent-plan/);
  assert.doesNotMatch(element('wf-activity-list').innerHTML, /agent-tia/);
});
