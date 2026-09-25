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
  assert.equal(symbols.ownKind({asset_id:'UAV-ONEWAY-01', role:'舰载自杀式无人机一号', domain:'air'}), 'loiterUav');
  assert.equal(symbols.ownKind({asset_id:'AEW-01', role:'舰载有人预警指挥机一号', domain:'air'}), 'aew');
  assert.equal(symbols.ownKind({asset_id:'UAV-STRIKE-01', role:'舰载攻击无人机01', domain:'air'}), 'strikeUav');
  for (const kind of ['aircraftCarrier', 'commandUav', 'tankerUav', 'aew', 'strikeUav', 'loiterUav']) {
    assert.match(symbols.svg(kind, 45), /<svg/);
  }
});

test('live weapons use distinct cruise, air-ground and one-way symbols', () => {
  const {window} = load('map/tactical-symbols.js');
  const symbols = window.TacticalSymbols;
  assert.equal(symbols.weaponKind({type:'SIM-LACM-01', weapon_type:'舰载对陆巡航导弹'}), 'cruiseMissile');
  assert.equal(symbols.weaponKind({type:'SIM-CARRIER-AGM-01', weapon_type:'舰载无人机空地导弹'}), 'airGroundMissile');
  assert.equal(symbols.weaponKind({type:'SIM-ONEWAY-UAV-01', weapon_type:'自杀式无人机战斗部'}), 'oneWayWeapon');
  assert.notEqual(symbols.svg('cruiseMissile', 0), symbols.svg('airGroundMissile', 0));
  assert.notEqual(symbols.svg('airGroundMissile', 0), symbols.svg('oneWayWeapon', 0));
  for (const kind of ['cruiseMissile', 'airGroundMissile', 'oneWayWeapon']) {
    assert.equal(symbols.affiliation(kind), 'friendly');
    assert.match(symbols.svg(kind, 25), /symbol-body/);
  }
});

test('authorization deferral re-prompts without sending a rejection command', () => {
  const source = readFileSync(resolve(__dirname, '../static/js/app/platform.js'), 'utf8');
  assert.match(source, /AUTHORIZATION_REMINDER_MS = 15000/);
  assert.match(source, /function deferAuthorizationDialog\(\)/);
  assert.match(source, /authorizationDeferredKey = authorizationPromptKey/);
  assert.match(source, /Date\.now\(\) < authorizationDeferredUntil/);
  assert.match(source, /仿真已暂停在阶段边界；稍后决定将再次提示/);
  assert.match(source, /authorization-cancel"\)\.addEventListener\("click", deferAuthorizationDialog\)/);
  const deferBody = source.slice(
    source.indexOf('function deferAuthorizationDialog()'),
    source.indexOf('function showAuthorizationDialog(')
  );
  assert.doesNotMatch(deferBody, /issueSimCommand|approved:\s*false|command_type:\s*["']reject/);
});

test('map uses displayed asset kinds for trails and separates coordinated impacts', () => {
  const source = readFileSync(resolve(__dirname, '../static/js/map/platform-map.js'), 'utf8');
  assert.match(source, /function ownTrailStyle\(asset\) \{\s*var kind = displayedOwnKind\(asset\)/);
  assert.match(source, /function weaponImpactGroupKey\(weapon\)/);
  assert.match(source, /协同 ['"] \+\s*\(index \+ 1\) \+ "\/" \+ count/);
  assert.match(source, /kind: weaponKind\(weapon\)/);
});

test('destroyed contacts preserve their target type instead of becoming ship wrecks', () => {
  const {window} = load('map/tactical-symbols.js');
  const symbols = window.TacticalSymbols;
  const destroyed = {agent_assessment:{damage_state:'destroyed', status:'confirmed', level:'high'}};
  assert.equal(symbols.trackKind({...destroyed, classification:'AIRFIELD_RUNWAY', domain:'ground'}), 'destroyedRunway');
  assert.equal(symbols.trackKind({...destroyed, classification:'MOBILE_COASTAL_AIR_DEFENSE', domain:'ground'}), 'destroyedRadarVehicle');
  assert.equal(symbols.trackKind({...destroyed, classification:'UNKNOWN', domain:'air'}), 'destroyedAir');
  assert.equal(symbols.trackKind({agent_assessment:{damage_state:'impact_pending'}, classification:'AIRFIELD_RUNWAY', domain:'ground'}), 'impactRunway');
  assert.equal(symbols.trackKind({agent_assessment:{}, classification:'CIVILIAN_PORT', domain:'ground'}), 'civilianGround');
  assert.equal(symbols.affiliation('destroyedRunway'), 'destroyed');
  assert.equal(symbols.affiliation('impactRadarVehicle'), 'impact');
  assert.match(symbols.svg('destroyedRunway', 20), /damage-overlay/);
  assert.match(symbols.svg('impactRadarVehicle', 20), /impact-overlay/);
  assert.match(symbols.svg('destroyedRunway', 20), /M28 9v8/);
  assert.doesNotMatch(symbols.svg('destroyedRunway', 20), /q7 3 14 0t14 0/);
});

test('backend-cleared fishing contacts keep civilian symbols without AIS', () => {
  const {window} = load('map/tactical-symbols.js');
  const symbols = window.TacticalSymbols;
  const fishing = {
    classification: 'FISHING_VESSEL', domain: 'maritime', ais_match: false,
    agent_assessment: {source: 'A2A 后端工作流', status: 'cleared', level: 'LOW', label: '低风险'},
  };
  assert.equal(symbols.trackKind(fishing), 'civilianSurface');
  assert.equal(symbols.trackKind({...fishing, agent_assessment: {}}), 'unknownSurface');
  assert.equal(symbols.trackKind({...fishing, agent_assessment: {...fishing.agent_assessment, damage_state: 'destroyed'}}), 'destroyedSurface');
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
