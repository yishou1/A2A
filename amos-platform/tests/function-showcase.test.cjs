const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {resolve} = require('node:path');
const vm = require('node:vm');

function loadShowcase() {
  const context = {window: {}, Object, Array, String, Number, Boolean, JSON, Math};
  vm.runInNewContext(
    readFileSync(resolve(__dirname, '../static/js/panels/function-showcase.js'), 'utf8'),
    context
  );
  return context.window.PlatformFunctionShowcase;
}

function loadInteractiveShowcase() {
  const rootListeners = {};
  const documentListeners = {};
  const root = {
    innerHTML: '',
    addEventListener(type, listener) { rootListeners[type] = listener; }
  };
  const document = {
    getElementById(id) {
      if (id === 'function-showcase-root') return root;
      if (id === 'function-showcase-algorithm-console') {
        return {getAttribute() { return '/algolib/algorithms'; }};
      }
      return null;
    },
    addEventListener(type, listener) { documentListeners[type] = listener; },
    dispatchEvent() {}
  };
  function CustomEvent(type, options) {
    this.type = type;
    this.detail = options && options.detail;
  }
  const context = {window: {}, document, CustomEvent, Object, Array, String, Number, Boolean, JSON, Math};
  vm.runInNewContext(
    readFileSync(resolve(__dirname, '../static/js/panels/function-showcase.js'), 'utf8'),
    context
  );
  const showcase = context.window.PlatformFunctionShowcase;
  showcase.init();
  return {showcase, root, rootListeners, documentListeners};
}

function view(overrides = {}) {
  const base = {
    schema_version: 'amos.workflow-view.v2',
    workflow_id: 'wf-1',
    run: {run_id: 'run-1', current: true},
    submission: {
      run_id: 'run-1', scenario_id: 'scene-1', checkpoint_id: 'CP-1',
      workflow_file: 'workflows/actual.bpel'
    },
    orchestration: {activities: []},
    activity_details: {},
    algorithms: {items: []},
    function_points: {items: []},
    agents: {instances: []}
  };
  return {...base, ...overrides};
}

const active = {runId: 'run-1', scenarioId: 'scene-1', scenarioName: '合成场景'};

test('accepts only a current workflow-view v2 from the active run', () => {
  const showcase = loadShowcase();
  const model = showcase.normalize(view(), active);
  assert.equal(model.available, true);
  assert.equal(model.runId, 'run-1');
  assert.equal(model.workflowId, 'wf-1');
  assert.equal(model.workflowFile, 'workflows/actual.bpel');
});

test('rejects a view explicitly marked as a previous run', () => {
  const showcase = loadShowcase();
  const model = showcase.normalize(view({run: {run_id: 'run-1', current: false}}), active);
  assert.equal(model.available, false);
});

test('isolates records whose workflow run id does not match the page context', () => {
  const showcase = loadShowcase();
  const model = showcase.normalize(view({run: {run_id: 'run-old', current: true}}), active);
  assert.equal(model.available, false);
});

test('keeps an invocation with no determinable activity owner at task level', () => {
  const showcase = loadShowcase();
  const invocation = {algorithm_id: 'alg-unknown', request_id: 'REQ-1', status: 'completed', output: {score: 1}};
  const model = showcase.normalize(view({
    orchestration: {activities: [{activity_id: 'A-1', status: 'running'}]},
    algorithms: {items: [{algorithm_id: 'alg-unknown', invocations: [invocation]}]}
  }), active);
  assert.equal(model.activities[0].calls.length, 0);
  assert.equal(model.taskCalls.length, 1);
  assert.equal(model.independentCallCount, 1);
});

test('uses only backend activity ids and activity references for conditional function association', () => {
  const showcase = loadShowcase();
  const model = showcase.normalize(view({
    orchestration: {activities: [
      {activity_id: 'A-1', work_item: 'Track', status: 'completed'},
      {activity_id: 'A-2', status: 'pending'}
    ]},
    function_points: {items: [{
      function_point_id: 'FP-C', name: '条件功能', scope: 'conditional',
      activity_ids: ['A-1'], evidence_refs: ['activity:A-2', 'trace:4'],
      actual_algorithms: [{activity_id: 'A-NOT-MAPPED'}]
    }]}
  }), active);
  assert.equal(model.functions[0].scope, 'conditional');
  assert.deepEqual(Array.from(model.functions[0].activityIds), ['A-1', 'A-2']);
});

test('shows failed invocation output without counting it as a successful result', () => {
  const showcase = loadShowcase();
  const failed = {algorithm_id: 'alg-fail', request_id: 'REQ-F', status: 'failed', output: {error: 'bad input'}};
  const model = showcase.normalize(view({
    orchestration: {activities: [{activity_id: 'A-F', status: 'failed'}]},
    activity_details: {'A-F': {algorithms: [{algorithm_id: 'alg-fail', invocations: [failed]}]}}
  }), active);
  assert.equal(model.independentCallCount, 1);
  assert.equal(model.successfulResultCount, 0);
  assert.deepEqual(model.activities[0].calls[0].invocation.output, {error: 'bad input'});
});

test('does not expose input, output or algorithm details for pending activities', () => {
  const showcase = loadShowcase();
  const model = showcase.normalize(view({
    orchestration: {activities: [{activity_id: 'A-P', status: 'pending', agent: 'agent-p'}]},
    activity_details: {'A-P': {
      input_fields: [{key: 'secret'}], output_fields: [{key: 'result'}],
      algorithms: [{algorithm_id: 'alg-p', invocations: [{request_id: 'REQ-P', status: 'completed'}]}]
    }}
  }), active);
  assert.equal(model.activities[0].started, false);
  assert.equal(model.activities[0].inputFieldCount, null);
  assert.equal(model.activities[0].outputFieldCount, null);
  assert.equal(model.activities[0].algorithms.length, 0);
  assert.equal(model.independentCallCount, 0);
});

test('preserves zero field counts, zero latency and Mock instance markers', () => {
  const showcase = loadShowcase();
  const zeroCall = {algorithm_id: 'alg-zero', status: 'completed', duration_ms: 0, input: {}, output: {count: 0}, execution_mode: 'mock'};
  const model = showcase.normalize(view({
    orchestration: {activities: [{activity_id: 'A-0', status: 'completed', execution_mode: 'mock'}]},
    activity_details: {'A-0': {input_fields: [], output_fields: [], algorithms: [{algorithm_id: 'alg-zero', execution_mode: 'mock', invocations: [zeroCall]}]}},
    agents: {instances: [{instance_id: 'mock-1', is_mock: true, call_count: 0, activity_count: 0}]}
  }), active);
  assert.equal(model.activities[0].inputFieldCount, 0);
  assert.equal(model.activities[0].outputFieldCount, 0);
  assert.equal(model.activities[0].calls.length, 1);
  assert.equal(model.activities[0].calls[0].invocation.duration_ms, 0);
  assert.equal(model.instances[0].is_mock, true);
});

test('labels inferred and activity-evidence records separately and excludes them from call totals', () => {
  const showcase = loadShowcase();
  const inferred = {
    algorithm_id: 'alg-derived', status: 'completed', input: {x: 1}, output: {y: 2},
    inferred_from_activity: true, execution_mode: 'activity_evidence',
    duration_source: 'activity_duration_fallback'
  };
  const model = showcase.normalize(view({
    orchestration: {activities: [{activity_id: 'A-D', status: 'completed'}]},
    activity_details: {'A-D': {algorithms: [{algorithm_id: 'alg-derived', invocations: [inferred]}]}}
  }), active);
  assert.equal(model.independentCallCount, 0);
  assert.equal(model.activities[0].derivedCallCount, 1);
});

test('does not promote a params-only algorithm configuration to an invocation', () => {
  const showcase = loadShowcase();
  const model = showcase.normalize(view({
    orchestration: {activities: [{activity_id: 'A-C', status: 'completed'}]},
    activity_details: {'A-C': {algorithms: [{
      algorithm_id: 'alg-config', version: '2', invocations: [{params: {threshold: 0.5}, execution_mode: 'onnx_runtime'}]
    }]}}
  }), active);
  assert.equal(model.activities[0].algorithms.length, 1);
  assert.equal(model.independentCallCount, 0);
});

test('does not duplicate one merged invocation across activities and retains real repeated calls', () => {
  const showcase = loadShowcase();
  const merged = {algorithm_id: 'alg-merged', request_id: 'REQ-M', status: 'completed', output: {ok: true}};
  const repeated = {algorithm_id: 'alg-repeat', status: 'completed', duration_ms: 4, input: {x: 1}};
  const model = showcase.normalize(view({
    orchestration: {activities: [
      {activity_id: 'A-1', status: 'completed'}, {activity_id: 'A-2', status: 'completed'},
      {activity_id: 'A-3', status: 'completed'}
    ]},
    activity_details: {
      'A-1': {algorithms: [{algorithm_id: 'alg-merged', evidence_refs: ['activity:A-1', 'activity:A-2'], invocations: [merged]}]},
      'A-2': {algorithms: [{algorithm_id: 'alg-merged', evidence_refs: ['activity:A-1', 'activity:A-2'], invocations: [merged]}]},
      'A-3': {algorithms: [{algorithm_id: 'alg-repeat', invocations: [repeated, repeated]}]}
    }
  }), active);
  assert.equal(model.taskCalls.length, 1);
  assert.equal(model.activities[0].calls.length, 0);
  assert.equal(model.activities[1].calls.length, 0);
  assert.equal(model.activities[2].calls.length, 2);
  assert.equal(model.independentCallCount, 3);
});

test('escapes backend-provided text before it can be inserted as HTML', () => {
  const showcase = loadShowcase();
  const escaped = showcase.escapeHtml('<img src=x onerror="attack()"> &\'');
  assert.equal(escaped, '&lt;img src=x onerror=&quot;attack()&quot;&gt; &amp;&#39;');
  assert.doesNotMatch(escaped, /<img|onerror="/);
});

test('retains completed task stages and function points while later workflow views arrive', () => {
  const {showcase, root} = loadInteractiveShowcase();
  showcase.setContext(active);
  showcase.receiveView(view({
    workflow_id: 'wf-stage-1',
    submission: {run_id: 'run-1', scenario_id: 'scene-1', checkpoint_id: 'CP-1', workflow_file: 'one.bpel'},
    orchestration: {activities: [{activity_id: 'A-1', status: 'completed'}]},
    function_points: {items: [{function_point_id: 'FP-1', name: '功能一', activity_ids: ['A-1']}]}
  }));
  showcase.receiveView(view({
    workflow_id: 'wf-stage-2',
    submission: {run_id: 'run-1', scenario_id: 'scene-1', checkpoint_id: 'CP-2', workflow_file: 'two.bpel'},
    orchestration: {activities: [{activity_id: 'A-2', status: 'running'}]},
    function_points: {items: [{function_point_id: 'FP-2', name: '功能二', evidence_refs: ['activity:A-2']}]}
  }));
  assert.match(root.innerHTML, /wf-stage-1/);
  assert.match(root.innerHTML, /wf-stage-2/);
  assert.match(root.innerHTML, /FP-1/);
  assert.match(root.innerHTML, /FP-2/);
  assert.equal((root.innerHTML.match(/function-showcase-stage"/g) || []).length, 2);
});

test('defers rerendering while the function filter has focus and applies queued updates on blur', () => {
  const {showcase, root, rootListeners} = loadInteractiveShowcase();
  showcase.setContext(active);
  showcase.receiveView(view({workflow_id: 'wf-stage-1'}));
  const filter = {id: 'function-showcase-filter'};
  rootListeners.focusin({target: filter});
  const before = root.innerHTML;
  showcase.receiveView(view({workflow_id: 'wf-stage-2'}));
  assert.equal(root.innerHTML, before);
  rootListeners.focusout({target: filter});
  assert.match(root.innerHTML, /wf-stage-2/);
});

test('clears accumulated stages when the active run changes', () => {
  const {showcase, root} = loadInteractiveShowcase();
  showcase.setContext(active);
  showcase.receiveView(view({workflow_id: 'wf-old-stage'}));
  showcase.setContext({runId: 'run-2', scenarioId: 'scene-1', scenarioName: '合成场景'});
  assert.doesNotMatch(root.innerHTML, /wf-old-stage/);
  assert.match(root.innerHTML, /尚未收到可展示的工作流记录/);
});
