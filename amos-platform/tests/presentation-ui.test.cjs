const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {resolve} = require('node:path');
const vm = require('node:vm');

function load(file) {
  const nodes = new Map();
  const document = {
    getElementById(id) {
      if (!nodes.has(id)) nodes.set(id, {innerHTML:'', textContent:''});
      return nodes.get(id);
    },
    querySelectorAll() { return [this.getElementById('status')]; }
  };
  const context = {document, window:{}, Set, Map, Date};
  vm.runInNewContext(readFileSync(resolve(__dirname, '../static/js', file), 'utf8'), context);
  return {...context, nodes};
}

test('resource labels preserve fuel, zero and missing data with full names', () => {
  const {window, document} = load('panels/platform-panels.js');
  window.PlatformPanels.updateAssetHealth([
    {id:'boat', role:'用于海洋气象观测的独立调查平台', domain:'maritime', fuel_pct:63},
    {id:'sat', domain:'space', battery_pct:0, comms_strength:0},
    {id:'unknown', role:'<img onerror=attack>', domain:'ground'}
  ]);
  const html = document.getElementById('asset-health-list').innerHTML;
  assert.match(html, /燃油 63%/);
  assert.match(html, /电量 0%/);
  assert.match(html, /太空/);
  assert.match(html, /链路 0%/);
  assert.match(html, /链路 未提供/);
  assert.match(html, /能源 未提供/);
  assert.match(html, /用于海洋气象观测的独立调查平台/);
  assert.doesNotMatch(html, /100%|<img onerror/);
});

test('offline runtime is unknown, connected empty runtime has zero packages', () => {
  const {window, document} = load('panels/platform-panels.js');
  window.PlatformPanels.updateRuntimeAlgorithms({status:'offline', algorithms:[]});
  for (const id of ['backend-runnable-count','backend-unavailable-count','backend-family-count']) {
    assert.equal(document.getElementById(id).textContent, '未知');
  }
  window.PlatformPanels.updateRuntimeAlgorithms({status:'ready', algorithms:[]});
  assert.equal(document.getElementById('backend-runnable-count').textContent, 0);
});

test('ONNX summary counts model packages, not unavailable implementations', () => {
  const {window, document} = load('panels/platform-panels.js');
  const algorithms = [
    {algorithm_id:'model_onnx', runtime_status:'ready'},
    {algorithm_id:'explicit_model', onnx_model_provided:true, runtime_status:'unavailable'},
    {algorithm_id:'ordinary', runtime_status:'unavailable'}
  ];
  for (const status of ['ready', 'degraded']) {
    window.PlatformPanels.updateRuntimeAlgorithms({status, algorithms, unavailable_count:9});
    assert.equal(document.getElementById('backend-unavailable-count').textContent, 2);
  }
  window.PlatformPanels.updateRuntimeAlgorithms({status:'offline', algorithms});
  assert.equal(document.getElementById('backend-unavailable-count').textContent, '未知');
});

test('carrier scenario resources use dedicated offline tactical symbols', () => {
  const {window} = load('map/tactical-symbols.js');
  const symbols = window.TacticalSymbols;
  assert.equal(symbols.ownKind({asset_id:'CV-01', role:'航母一号', domain:'maritime'}), 'aircraftCarrier');
  assert.equal(symbols.ownKind({asset_id:'UAV-C2-01', role:'舰载指挥中继无人机一号', domain:'air'}), 'commandUav');
  assert.equal(symbols.ownKind({asset_id:'UAV-TANKER-01', role:'舰载无人加油/通信中继机一号', domain:'air'}), 'tankerUav');
  assert.equal(symbols.ownKind({asset_id:'AEW-01', role:'舰载有人预警指挥机一号', domain:'air'}), 'aew');
  assert.equal(symbols.ownKind({asset_id:'UAV-STRIKE-01', role:'舰载攻击无人机01', domain:'air'}), 'strikeUav');
  assert.equal(symbols.ownKind({asset_id:'LOITER-UAV-01', role:'巡飞攻击无人机01', domain:'air'}), 'loiterUav');
  for (const kind of ['aircraftCarrier', 'commandUav', 'tankerUav', 'aew', 'strikeUav', 'loiterUav']) {
    assert.match(symbols.svg(kind, 45), /<svg/);
  }
});

test('new land-target classifications use operator-facing Chinese labels', () => {
  const {window} = load('panels/platform-panels.js');
  const assessed = {agent_assessment:{source:'backend'}};
  assert.match(window.PlatformPanels.contactLabel({
    ...assessed, id:'airfield', display_label:'地面接触 01', classification:'AIRFIELD_RUNWAY'
  }), /军用机场跑道/);
  assert.match(window.PlatformPanels.contactLabel({
    ...assessed, id:'mobile-ad', display_label:'地面接触 02', classification:'MOBILE_COASTAL_AIR_DEFENSE'
  }), /机动岸防雷达\/防空单元/);
  assert.match(window.PlatformPanels.contactLabel({
    ...assessed, id:'port', display_label:'地面接触 03', classification:'CIVILIAN_PORT'
  }), /民用渔港/);
});
