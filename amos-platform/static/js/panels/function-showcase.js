/* function-showcase.js — read-only projection of amos.workflow-view.v2 */

window.PlatformFunctionShowcase = (function () {
  "use strict";

  var root = null;
  var context = {scenarioId: null, scenarioName: null, runId: null};
  var viewsById = {};
  var viewOrder = [];
  var pendingViews = [];
  var selectedFunctionId = "__all__";
  var selectedCoverageFunctionId = "__all__";
  var coverageData = null;
  var renderSuspended = false;
  var renderPending = false;

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function hasOwn(object, key) {
    return Boolean(object) && Object.prototype.hasOwnProperty.call(object, key);
  }

  function valueOrUnknown(value) {
    return value === 0 || value === false || value ? String(value) : "未知";
  }

  function statusLabel(value) {
    return ({
      queued: "等待执行", pending: "待执行", running: "执行中", executing: "执行中",
      completed: "已完成", succeeded: "成功", success: "成功", ok: "成功",
      failed: "失败", error: "错误", cancelled: "已取消", conditional: "条件功能",
      not_applicable: "本任务不适用", declared: "未执行", unknown: "未上报",
    })[String(value || "").toLowerCase()] || value || "未上报";
  }

  function activityId(activity) {
    return String(activity && (activity.activity_id || activity.work_item || ("activity-" + activity.index)) || "");
  }

  function activityAliases(activity) {
    return [activity && activity.activity_id, activity && activity.work_item, activityId(activity)]
      .filter(Boolean).map(String);
  }

  function isStarted(activity) {
    if (!activity) return false;
    if (activity.started_at) return true;
    return ["running", "executing", "completed", "succeeded", "success", "failed", "error"]
      .indexOf(String(activity.status || "").toLowerCase()) >= 0;
  }

  function isSuccess(value) {
    return ["completed", "succeeded", "success", "ok"]
      .indexOf(String(value || "").toLowerCase()) >= 0;
  }

  function isFailure(value) {
    return ["failed", "error"].indexOf(String(value || "").toLowerCase()) >= 0;
  }

  function fieldCount(detail, fieldKey, detailKey) {
    if (!detail) return null;
    if (hasOwn(detail, fieldKey) && Array.isArray(detail[fieldKey])) return detail[fieldKey].length;
    if (hasOwn(detail, detailKey) && detail[detailKey] && typeof detail[detailKey] === "object") {
      return Array.isArray(detail[detailKey]) ? detail[detailKey].length : Object.keys(detail[detailKey]).length;
    }
    return null;
  }

  function payloadFieldCount(value) {
    if (value == null) return null;
    if (Array.isArray(value)) return value.length;
    if (typeof value === "object") return Object.keys(value).length;
    return 1;
  }

  function detailFor(view, activity) {
    var details = view && view.activity_details || {};
    var aliases = activityAliases(activity);
    for (var i = 0; i < aliases.length; i += 1) {
      if (details[aliases[i]]) return details[aliases[i]];
    }
    return null;
  }

  function canonical(value) {
    if (Array.isArray(value)) return "[" + value.map(canonical).join(",") + "]";
    if (value && typeof value === "object") {
      return "{" + Object.keys(value).sort().map(function (key) {
        return JSON.stringify(key) + ":" + canonical(value[key]);
      }).join(",") + "}";
    }
    return JSON.stringify(value);
  }

  function invocationKey(invocation, algorithmId) {
    if (invocation.request_id) return String(algorithmId) + ":request:" + invocation.request_id;
    if (invocation.trace_id) return String(algorithmId) + ":trace:" + invocation.trace_id;
    return String(algorithmId) + ":record:" + canonical(invocation);
  }

  function hasInvocationEvidence(invocation) {
    if (!invocation || typeof invocation !== "object") return false;
    if (invocation.request_id || invocation.trace_id || invocation.error) return true;
    if (hasOwn(invocation, "input") || hasOwn(invocation, "output") || hasOwn(invocation, "usage")) return true;
    if ((hasOwn(invocation, "duration_ms") && invocation.duration_ms != null && Number.isFinite(Number(invocation.duration_ms))) ||
        (hasOwn(invocation, "latency_ms") && invocation.latency_ms != null && Number.isFinite(Number(invocation.latency_ms)))) return true;
    return ["running", "executing", "completed", "succeeded", "success", "ok", "failed", "error"]
      .indexOf(String(invocation.status || "").toLowerCase()) >= 0;
  }

  function isDerivedInvocation(invocation, algorithm) {
    var mode = String(invocation.execution_mode || algorithm.execution_mode || "").toLowerCase();
    var source = String(invocation.duration_source || algorithm.duration_source || "").toLowerCase();
    return Boolean(invocation.inferred_from_activity) || mode === "activity_evidence" ||
      source === "activity_duration_fallback";
  }

  function referencedActivities(algorithm, aliasMap) {
    var result = [];
    (algorithm.evidence_refs || []).forEach(function (ref) {
      var value = String(ref || "");
      if (value.indexOf("activity:") !== 0) return;
      var resolved = aliasMap[value.slice(9)];
      if (resolved && result.indexOf(resolved) < 0) result.push(resolved);
    });
    (algorithm.activity_ids || []).forEach(function (id) {
      var resolved = aliasMap[String(id)];
      if (resolved && result.indexOf(resolved) < 0) result.push(resolved);
    });
    return result;
  }

  function collectCalls(view, activities, aliasMap) {
    var groups = {};
    var derivedKeys = {};
    var startedIds = {};
    activities.forEach(function (activity) { if (activity.started) startedIds[activity.id] = true; });

    function register(invocation, algorithm, sourceActivityId, sourceKey) {
      if (!invocation || !hasInvocationEvidence(invocation)) return;
      var algorithmId = invocation.algorithm_id || algorithm.algorithm_id || algorithm.name || "未标识算法";
      var key = invocationKey(invocation, algorithmId);
      var explicit = aliasMap[String(invocation.activity_id || invocation.work_item || "")];
      var owners = explicit && startedIds[explicit] ? [explicit] : referencedActivities(algorithm, aliasMap).filter(function (id) {
        return Boolean(startedIds[id]);
      });
      if (!owners.length && sourceActivityId && startedIds[sourceActivityId]) owners = [sourceActivityId];
      if (!owners.length && !sourceActivityId && !Object.keys(startedIds).length) return;
      if (isDerivedInvocation(invocation, algorithm)) {
        var derived = derivedKeys[key] || (derivedKeys[key] = {algorithmId: String(algorithmId), ownerMap: {}});
        owners.forEach(function (owner) { derived.ownerMap[owner] = true; });
        return;
      }
      var group = groups[key] || (groups[key] = {
        key: key,
        algorithmId: String(algorithmId),
        invocation: invocation,
        algorithm: algorithm,
        owners: {},
        counts: {},
        identified: Boolean(invocation.request_id || invocation.trace_id),
      });
      owners.forEach(function (owner) { group.owners[owner] = true; });
      group.counts[sourceKey] = (group.counts[sourceKey] || 0) + 1;
    }

    activities.forEach(function (activity) {
      if (!isStarted(activity.raw)) return;
      (activity.algorithms || []).forEach(function (algorithm) {
        (Array.isArray(algorithm.invocations) ? algorithm.invocations : []).forEach(function (invocation) {
          register(invocation, algorithm, activity.id, "activity:" + activity.id);
        });
      });
    });
    ((view.algorithms || {}).items || []).forEach(function (algorithm) {
      (Array.isArray(algorithm.invocations) ? algorithm.invocations : []).forEach(function (invocation) {
        register(invocation, algorithm, null, "task");
      });
    });

    var byActivity = {};
    activities.forEach(function (activity) { byActivity[activity.id] = []; });
    var task = [];
    Object.keys(groups).forEach(function (key) {
      var group = groups[key];
      var owners = Object.keys(group.owners).filter(function (id) {
        return Boolean(byActivity[id]);
      });
      var counts = Object.keys(group.counts).map(function (source) { return group.counts[source]; });
      var copies = group.identified ? 1 : Math.max.apply(Math, counts.length ? counts : [1]);
      var destination = owners.length === 1 ? byActivity[owners[0]] : task;
      for (var index = 0; index < copies; index += 1) destination.push(group);
    });

    var derivedByActivity = {};
    activities.forEach(function (activity) { derivedByActivity[activity.id] = 0; });
    var taskDerived = 0;
    Object.keys(derivedKeys).forEach(function (key) {
      var owners = Object.keys(derivedKeys[key].ownerMap).filter(function (id) { return hasOwn(derivedByActivity, id); });
      if (owners.length === 1) derivedByActivity[owners[0]] += 1;
      else taskDerived += 1;
    });
    return {byActivity: byActivity, task: task, derivedByActivity: derivedByActivity, taskDerived: taskDerived};
  }

  function validView(view, activeContext) {
    if (!view || view.schema_version !== "amos.workflow-view.v2") return false;
    if (!activeContext || !activeContext.runId) return false;
    var viewRunId = view.run && view.run.run_id || view.submission && view.submission.run_id;
    if (!viewRunId || String(viewRunId) !== String(activeContext.runId)) return false;
    if (view.run && view.run.current === false) return false;
    var viewScenarioId = view.submission && view.submission.scenario_id;
    if (activeContext.scenarioId && viewScenarioId && String(viewScenarioId) !== String(activeContext.scenarioId)) return false;
    return true;
  }

  function normalize(view, activeContext) {
    activeContext = activeContext || {};
    if (!validView(view, activeContext)) {
      return {available: false, reason: "当前查看任务没有与本轮运行匹配的工作流记录"};
    }
    var rawActivities = view.orchestration && view.orchestration.activities || [];
    var aliasMap = {};
    rawActivities.forEach(function (activity) {
      var id = activityId(activity);
      activityAliases(activity).forEach(function (alias) { aliasMap[alias] = id; });
    });
    var activities = rawActivities.map(function (activity) {
      var id = activityId(activity);
      var detail = detailFor(view, activity);
      var started = isStarted(activity);
      return {
        id: id,
        title: activity.role || activity.work_item || activity.activity_id || id,
        status: activity.status || "unknown",
        started: started,
        agent: activity.agent || detail && detail.agent_call && detail.agent_call.agent || null,
        instanceId: activity.instance_id || detail && detail.agent_call && detail.agent_call.instance_id || null,
        executionMode: activity.execution_mode || detail && detail.agent_call && detail.agent_call.execution_mode || null,
        inputFieldCount: started ? fieldCount(detail, "input_fields", "input_detail") : null,
        outputFieldCount: started ? fieldCount(detail, "output_fields", "output_detail") : null,
        algorithms: started && detail && Array.isArray(detail.algorithms) ? detail.algorithms : [],
        raw: activity,
      };
    });
    var calls = collectCalls(view, activities, aliasMap);
    activities.forEach(function (activity) {
      activity.calls = calls.byActivity[activity.id] || [];
      activity.derivedCallCount = calls.derivedByActivity[activity.id] || 0;
    });

    var functions = ((view.function_points || {}).items || []).map(function (item) {
      var ids = [];
      (item.activity_ids || []).forEach(function (id) {
        var resolved = aliasMap[String(id)];
        if (resolved && ids.indexOf(resolved) < 0) ids.push(resolved);
      });
      (item.evidence_refs || []).forEach(function (ref) {
        var value = String(ref || "");
        var resolved = value.indexOf("activity:") === 0 ? aliasMap[value.slice(9)] : null;
        if (resolved && ids.indexOf(resolved) < 0) ids.push(resolved);
      });
      return {
        id: String(item.function_point_id || item.function_id || item.id || item.name || ""),
        name: item.name || item.function_point_id || item.function_id || "未命名功能",
        scope: item.scope || (item.status === "conditional" ? "conditional" : null),
        activityIds: ids,
      };
    }).filter(function (item) { return Boolean(item.id); });

    var independentCalls = activities.reduce(function (total, activity) { return total + activity.calls.length; }, calls.task.length);
    var successfulResults = activities.reduce(function (total, activity) {
      return total + activity.calls.filter(function (call) {
        return isSuccess(call.invocation.status) && hasOwn(call.invocation, "output") && call.invocation.output != null;
      }).length;
    }, calls.task.filter(function (call) {
      return isSuccess(call.invocation.status) && hasOwn(call.invocation, "output") && call.invocation.output != null;
    }).length);
    var submission = view.submission || {};
    var stage = submission.stage_transfer || {};
    var instances = view.agents && Array.isArray(view.agents.instances) ? view.agents.instances : [];
    return {
      available: true,
      scenarioId: activeContext.scenarioId,
      scenarioName: activeContext.scenarioName,
      runId: activeContext.runId,
      workflowId: view.workflow_id,
      checkpoint: stage.checkpoint_id || submission.checkpoint_id || null,
      workflowFile: submission.workflow_file || null,
      activities: activities,
      functions: functions,
      taskCalls: calls.task,
      taskDerivedCallCount: calls.taskDerived,
      instances: instances,
      independentCallCount: independentCalls,
      successfulResultCount: successfulResults,
    };
  }

  function definitionRows(rows) {
    return '<dl class="function-showcase-definitions">' + rows.map(function (row) {
      return '<div><dt>' + escapeHtml(row[0]) + '</dt><dd>' + escapeHtml(valueOrUnknown(row[1])) + '</dd></div>';
    }).join("") + "</dl>";
  }

  function callHtml(call, taskLevel) {
    var invocation = call.invocation || {};
    var algorithm = call.algorithm || {};
    var failed = isFailure(invocation.status);
    var resultLabel = failed ? "错误响应" : "返回结果";
    var result = hasOwn(invocation, "output") ? invocation.output : invocation.error;
    var duration = hasOwn(invocation, "duration_ms") && invocation.duration_ms != null
      ? invocation.duration_ms : invocation.latency_ms;
    return '<details class="function-showcase-call ' + (failed ? "failed" : "") + '">' +
      '<summary><b>' + escapeHtml(call.algorithmId) + '</b><span>' +
        escapeHtml(taskLevel ? "任务级记录 · 归属未上报" : statusLabel(invocation.status)) + '</span></summary>' +
      definitionRows([
        ["状态", statusLabel(invocation.status)],
        ["版本", invocation.version || algorithm.version],
        ["模式", invocation.execution_mode || algorithm.execution_mode],
        ["后端", invocation.backend_type || algorithm.backend_type],
        ["请求 ID", invocation.request_id],
        ["耗时（ms）", duration],
        ["输入字段", payloadFieldCount(invocation.input)],
        [resultLabel + "字段", payloadFieldCount(result)],
      ]) +
      (result != null ? '<div class="function-showcase-result"><b>' + escapeHtml(resultLabel) + '</b><pre>' +
        escapeHtml(typeof result === "string" ? result : JSON.stringify(result, null, 2)) + '</pre></div>' : '') +
    '</details>';
  }

  function algorithmHtml(algorithm, calls) {
    var algorithmId = algorithm.algorithm_id || algorithm.name || "未标识算法";
    return '<div class="function-showcase-algorithm"><b>' + escapeHtml(algorithmId) + '</b><span>' +
      escapeHtml("版本 " + valueOrUnknown(algorithm.version) + " · 模式 " + valueOrUnknown(algorithm.execution_mode) +
        " · 后端 " + valueOrUnknown(algorithm.backend_type)) + '</span><em>' +
      escapeHtml((calls || []).filter(function (call) { return call.algorithmId === String(algorithmId); }).length + " 条独立调用") +
    '</em></div>';
  }

  function checkpointLabel(checkpoint) {
    var value = String(checkpoint || "");
    var labels = {
      PERCEPTION: "观测与目标识别", CUE: "目标发现与识别", SAT: "天基发现与识别",
      FIX: "多源融合与目标确认", IDENTIFY: "目标确认与身份研判", FUSION: "多源融合与校验",
      TRACK: "航迹与威胁评估", ASSESS: "威胁评估与结果复核", PLAN: "方案规划与授权准备",
      ENGAGE: "交战授权与执行", WAVE1: "第一波交战执行", WAVE2: "第二波攻击与复核",
      CLOSE: "收尾评估与任务闭环",
    };
    var suffix = value.split("-").pop();
    return labels[suffix] || (value ? "任务阶段 " + value : "等待任务阶段");
  }

  function shortWorkflowFile(value) {
    var text = String(value || "");
    var parts = text.split(/[\\/]/);
    return parts[parts.length - 1] || text || "未上报";
  }

  function completedActivityCount(activities) {
    return (activities || []).filter(function (activity) {
      return isSuccess(activity.status);
    }).length;
  }

  function evidenceLabel(activity) {
    var hasResult = activity.calls.some(function (call) {
      return isSuccess(call.invocation.status) && hasOwn(call.invocation, "output") && call.invocation.output != null;
    });
    if (!activity.started) return "等待执行";
    if (isFailure(activity.status)) return "执行失败";
    if (hasResult || activity.outputFieldCount != null) return "已形成结果";
    return "已执行，结果待回传";
  }

  function activityHtml(activity, model) {
    var pending = !activity.started;
    var evidenceCount = activity.calls.length + activity.derivedCallCount;
    return '<article class="function-showcase-activity ' + escapeHtml(String(activity.status || "unknown")) + '">' +
      '<header><div><b>' + escapeHtml(activity.title) + '</b><small>' + escapeHtml(activity.agent ? "负责角色 " + activity.agent : "后端工作流节点") + '</small></div>' +
        '<span>' + escapeHtml(statusLabel(activity.status)) + '</span></header>' +
      '<div class="function-showcase-business-evidence">' +
        '<span><small>节点状态</small><b>' + escapeHtml(statusLabel(activity.status)) + '</b></span>' +
        '<span><small>结果状态</small><b>' + escapeHtml(evidenceLabel(activity)) + '</b></span>' +
        '<span><small>关联证据</small><b>' + escapeHtml(evidenceCount ? evidenceCount + " 项" : "待上报") + '</b></span>' +
      '</div>' +
      (pending ? '<p class="function-showcase-muted">该阶段尚未执行，执行后将在任务检查器中查看输入、结果和算法证据。</p>' :
        '<p class="function-showcase-muted">该阶段的业务结果已纳入本轮运行记录，可打开任务检查器查看完整证据。</p>') +
      '<footer><button type="button" class="btn btn-sm" data-showcase-activity="' + escapeHtml(activity.id) +
        '" data-showcase-workflow="' + escapeHtml(model.workflowId) + '">查看执行证据</button></footer>' +
    '</article>';
  }

  function instanceHtml(record) {
    var instance = record.instance;
    var flags = [];
    if (instance.is_mock) flags.push("Mock");
    if (instance.is_stub) flags.push("Stub");
    if (instance.real_service) flags.push("服务实例");
    return '<article class="function-showcase-instance"><header><b>' +
      escapeHtml(instance.agent || instance.instance_id || "未命名实例") + '</b><span>' +
      escapeHtml(statusLabel(instance.status)) + '</span></header>' +
      definitionRows([
        ["实例 ID", instance.instance_id], ["角色", instance.role], ["模式", instance.execution_mode],
        ["标记", flags.join(" / ") || null], ["活动数", hasOwn(instance, "activity_count") ? instance.activity_count : null],
        ["调用数", hasOwn(instance, "call_count") ? instance.call_count : null], ["任务阶段", record.checkpoint || record.workflowId],
      ]) + '</article>';
  }

  function resetViews() {
    viewsById = {};
    viewOrder = [];
    pendingViews = [];
    selectedFunctionId = "__all__";
    selectedCoverageFunctionId = "__all__";
  }

  function coverageKindLabel(kind) {
    return ({
      primary: "主覆盖", supporting: "辅助覆盖", conditional: "条件覆盖",
      not_covered: "未覆盖",
    })[String(kind || "")] || "设计覆盖";
  }

  function coverageKindClass(kind) {
    return String(kind || "planned").replace(/[^a-z_]/g, "-");
  }

  function coverageScenario(scenarioId) {
    return (coverageData && coverageData.scenarios || []).find(function (item) {
      return String(item.id) === String(scenarioId);
    });
  }

  function coverageItem(functionId) {
    return (coverageData && coverageData.items || []).find(function (item) {
      return String(item.function_point_id) === String(functionId);
    });
  }

  function runtimeCoverageSummary(items, models) {
    var byId = {};
    (models || []).forEach(function (model) {
      var activityById = {};
      (model.activities || []).forEach(function (activity) {
        activityById[activity.id] = activity;
      });
      (model.functions || []).forEach(function (point) {
        var state = byId[point.id] || (byId[point.id] = {seen: false, verified: false});
        state.seen = true;
        (point.activityIds || []).forEach(function (activityId) {
          var activity = activityById[activityId];
          if (activity && isSuccess(activity.status)) state.verified = true;
        });
      });
    });
    var designCovered = items.filter(function (item) {
      return Number(item.covered_scenario_count || 0) > 0;
    }).length;
    var verified = items.filter(function (item) {
      return Boolean(byId[item.function_point_id] && byId[item.function_point_id].verified);
    }).length;
    var conditionalPending = items.filter(function (item) {
      var conditional = (item.scenarios || []).some(function (row) {
        return row.coverage_kind === "conditional";
      });
      return conditional && !(byId[item.function_point_id] && byId[item.function_point_id].verified);
    }).length;
    return {
      designCovered: designCovered,
      designTotal: Number(coverageData && coverageData.total_function_points || items.length),
      verified: verified,
      conditionalPending: conditionalPending,
      notExecuted: Math.max(items.length - verified - conditionalPending, 0),
    };
  }

  function coverageHtml(models) {
    if (!coverageData || !Array.isArray(coverageData.scenarios)) return "";
    var scenarios = coverageData.scenarios;
    var items = Array.isArray(coverageData.items) ? coverageData.items : [];
    var summary = runtimeCoverageSummary(items, models);
    var selected = selectedCoverageFunctionId !== "__all__" ? coverageItem(selectedCoverageFunctionId) : null;
    var stageGroups = {observe: "OBSERVE 观测", orient: "ORIENT 判断", decide: "DECIDE 决策", act: "ACT / ASSESS 执行评估"};
    var grouped = {};
    items.forEach(function (item) {
      var key = item.ooda_phase || "act";
      (grouped[key] || (grouped[key] = [])).push(item);
    });
    var matrixRows = Object.keys(stageGroups).map(function (phase) {
      var rows = grouped[phase] || [];
      if (!rows.length) return "";
      return '<tr class="function-showcase-matrix-group"><th colspan="' + (scenarios.length + 1) + '">' + escapeHtml(stageGroups[phase]) + '</th></tr>' +
        rows.map(function (item) {
          return '<tr><th><button type="button" class="function-showcase-matrix-function" data-showcase-coverage-function="' + escapeHtml(item.function_point_id) + '"><code>' +
            escapeHtml(item.function_point_id) + '</code><span>' + escapeHtml(item.chinese_name || item.name) + '</span></button></th>' +
            scenarios.map(function (scenario) {
              var row = (item.scenarios || []).find(function (entry) { return String(entry.scenario_id) === String(scenario.id); }) || {};
              return '<td><button type="button" class="function-showcase-coverage-cell ' + coverageKindClass(row.coverage_kind) + '" data-showcase-coverage-function="' +
                escapeHtml(item.function_point_id) + '" title="' + escapeHtml(coverageKindLabel(row.coverage_kind)) + '">' + escapeHtml(coverageKindLabel(row.coverage_kind)) + '</button></td>';
            }).join("") + '</tr>';
        }).join("");
    }).join("");
    var detailHtml = selected ? '<div class="function-showcase-coverage-detail"><div class="function-showcase-heading"><div><small>功能点详情</small><h3>' +
      escapeHtml(selected.function_point_id + " · " + (selected.chinese_name || selected.name)) + '</h3></div><span>' +
      escapeHtml((selected.english_name || "") + " / " + String(selected.ooda_phase || "").toUpperCase()) + '</span></div><div class="function-showcase-coverage-detail-grid">' +
      scenarios.map(function (scenario) {
        var row = (selected.scenarios || []).find(function (entry) { return String(entry.scenario_id) === String(scenario.id); }) || {};
        return '<article><b>' + escapeHtml(scenario.name) + '</b><span class="function-showcase-coverage-badge ' + coverageKindClass(row.coverage_kind) + '">' +
          escapeHtml(coverageKindLabel(row.coverage_kind)) + '</span><small>' + escapeHtml((row.checkpoints || []).join(" / ") || "由剧本链路承载") + '</small><em>' +
          escapeHtml(scenario.workflow_chain_label || scenario.workflow_chain_id || "场景专用工作流链") + '</em></article>';
      }).join("") + '</div></div>' : '<p class="function-showcase-muted">点击矩阵中的功能点，可查看它在三个剧本中的覆盖角色和工作流链归属。</p>';
    return '<section class="function-showcase-card function-showcase-coverage"><div class="function-showcase-heading"><div><small>面向甲方的设计覆盖总览</small><h2>三剧本功能覆盖矩阵</h2></div><span>设计态 + 运行态分开</span></div>' +
      '<p class="function-showcase-muted">设计覆盖说明“三个剧本计划覆盖什么”，运行指标说明“当前这一次演示实际验证了什么”。</p>' +
      '<div class="function-showcase-coverage-metrics"><span><small>设计覆盖</small><b>' + escapeHtml(summary.designCovered + "/" + summary.designTotal) + '</b></span><span><small>本轮已验证</small><b>' + escapeHtml(summary.verified + "/" + summary.designTotal) + '</b></span><span><small>条件待触发</small><b>' + escapeHtml(summary.conditionalPending) + '</b></span><span><small>尚未执行</small><b>' + escapeHtml(summary.notExecuted) + '</b></span></div>' +
      '<div class="function-showcase-legend"><span class="primary">主覆盖</span><span class="supporting">辅助覆盖</span><span class="conditional">条件覆盖</span><span class="not_covered">未覆盖</span></div>' +
      '<div class="function-showcase-matrix-wrap"><table class="function-showcase-matrix"><thead><tr><th>功能点</th>' + scenarios.map(function (scenario) { return '<th>' + escapeHtml(scenario.name) + '</th>'; }).join("") + '</tr></thead><tbody>' + matrixRows + '</tbody></table></div>' + detailHtml + '</section>';
  }

  function upsertView(view) {
    if (!validView(view, context)) return false;
    var id = String(view.workflow_id || "");
    if (!id) return false;
    if (!hasOwn(viewsById, id)) viewOrder.push(id);
    viewsById[id] = view;
    return true;
  }

  function modelsForContext() {
    return viewOrder.map(function (id) {
      return normalize(viewsById[id], context);
    }).filter(function (model) { return model.available; });
  }

  function requestRender() {
    if (renderSuspended) {
      renderPending = true;
      return;
    }
    renderPending = false;
    render();
  }

  function aggregateFunctions(models) {
    var byId = {};
    var order = [];
    models.forEach(function (model) {
      model.functions.forEach(function (item) {
        var aggregate = byId[item.id];
        if (!aggregate) {
          aggregate = byId[item.id] = {
            id: item.id, name: item.name, scope: item.scope,
            activityCount: 0, stageNames: [],
          };
          order.push(item.id);
        }
        if (item.scope === "conditional") aggregate.scope = "conditional";
        aggregate.activityCount += item.activityIds.length;
        if (item.activityIds.length) {
          var stageLabel = checkpointLabel(model.checkpoint);
          if (aggregate.stageNames.indexOf(stageLabel) < 0) aggregate.stageNames.push(stageLabel);
        }
      });
    });
    return order.map(function (id) { return byId[id]; });
  }

  function functionPointHtml(item) {
    var scope = item.scope === "conditional" ? "条件功能" : "后端功能";
    var stageText = item.stageNames.length ? item.stageNames.join("、") : "尚未关联运行阶段";
    return '<button type="button" class="function-showcase-function' +
      (item.id === selectedFunctionId ? ' selected' : '') + '" data-showcase-function="' + escapeHtml(item.id) + '">' +
      '<b>' + escapeHtml(item.name) + '</b><small>' + escapeHtml("功能点 " + item.id + " · " + scope +
        " · 已关联阶段：" + stageText) + '</small></button>';
  }

  function stageHtml(model, index) {
    var selected = model.functions.find(function (item) { return item.id === selectedFunctionId; });
    var visibleActivities = selected ? model.activities.filter(function (activity) {
      return selected.activityIds.indexOf(activity.id) >= 0;
    }) : (selectedFunctionId === "__all__" ? model.activities : []);
    if (selectedFunctionId !== "__all__" && !selected) return "";
    var stageLabel = model.checkpoint || "未上报检查点";
    var completed = completedActivityCount(model.activities);
    return '<section class="function-showcase-card function-showcase-stage">' +
      '<div class="function-showcase-heading"><div><small>第 ' + escapeHtml(index + 1) + ' 个分析阶段</small><h2>' +
        escapeHtml(checkpointLabel(stageLabel)) + '</h2></div><span>' +
        escapeHtml(visibleActivities.length + " 项活动") + '</span></div>' +
      definitionRows([["业务阶段", checkpointLabel(stageLabel)], ["执行链", shortWorkflowFile(model.workflowFile)],
        ["节点进度", completed + "/" + model.activities.length + " 已完成"],
        ["结果证据", model.successfulResultCount ? model.successfulResultCount + " 项已返回" : "等待返回"]]) +
      '<div class="function-showcase-stage-activities"><div class="function-showcase-activity-list">' +
        (visibleActivities.length ? visibleActivities.map(function (activity) { return activityHtml(activity, model); }).join("") :
          '<div class="function-showcase-empty compact">后端未提供该功能在此任务阶段的关联活动</div>') +
      '</div></div>' +
      (model.taskCalls.length || model.taskDerivedCallCount ? '<div class="function-showcase-task-calls"><h3>任务级调用记录 <small>活动归属未上报 · ' +
        escapeHtml(model.taskCalls.length + " 条") + '</small></h3><p class="function-showcase-muted">后台分析证据已记录，请在任务执行检查器中查看明细。</p></div>' : '') +
    '</section>';
  }

  function render() {
    if (!root) return;
    var models = modelsForContext();
    if (!models.length) {
      root.innerHTML = coverageHtml(models) + '<div class="function-showcase-empty">当前运行尚未收到可展示的工作流记录</div>';
      return;
    }
    var functions = aggregateFunctions(models);
    if (selectedFunctionId !== "__all__" && !functions.some(function (item) { return item.id === selectedFunctionId; })) {
      selectedFunctionId = "__all__";
    }
    var activityCount = models.reduce(function (total, model) { return total + model.activities.length; }, 0);
    var completedActivities = models.reduce(function (total, model) {
      return total + completedActivityCount(model.activities);
    }, 0);
    var successfulResultCount = models.reduce(function (total, model) {
      return total + model.successfulResultCount;
    }, 0);
    var latestModel = models[models.length - 1];
    root.innerHTML =
      coverageHtml(models) +
      '<p class="function-showcase-scope">本页面向能力演示，突出当前剧本覆盖的功能、执行阶段和结果状态。完整的输入、算法调用和原始结果请在“流程执行”中查看。</p>' +
      '<section class="function-showcase-card"><div class="function-showcase-heading"><div><small>当前剧本运行</small><h2>' +
        escapeHtml(context.scenarioName || context.scenarioId || "未上报场景") + '</h2></div><span>' +
        escapeHtml(checkpointLabel(latestModel.checkpoint)) + '</span></div>' +
        definitionRows([["当前阶段", checkpointLabel(latestModel.checkpoint)], ["已完成节点", completedActivities + "/" + activityCount],
          ["已接收分析阶段", models.length], ["已形成结果", successfulResultCount + " 项"]]) +
      '</section>' +
      '<div class="function-showcase-metrics"><span><small>分析任务</small><b>' + models.length + '</b></span>' +
        '<span><small>本轮功能点</small><b>' + functions.length + '</b></span>' +
        '<span><small>已完成节点</small><b>' + completedActivities + '/' + activityCount + '</b></span>' +
        '<span><small>结果证据</small><b>' + successfulResultCount + '</b></span></div>' +
      '<section class="function-showcase-card"><div class="function-showcase-heading"><div><small>后端活动关联</small><h2>功能关联</h2></div></div>' +
        '<label class="function-showcase-filter">查看功能<select id="function-showcase-filter"><option value="__all__">全部功能与活动</option>' +
          functions.map(function (item) {
            var suffix = item.scope === "conditional" ? "（条件功能）" : "";
            return '<option value="' + escapeHtml(item.id) + '"' + (item.id === selectedFunctionId ? " selected" : "") + '>' +
              escapeHtml(item.id + " · " + item.name + suffix + " · " +
                (item.stageNames.length ? item.stageNames.join("、") : "尚未关联运行阶段")) + '</option>';
          }).join("") + '</select></label>' +
        '<p class="function-showcase-muted">下列功能点按本轮已收到的任务持续保留。点击功能点可查看它对应的业务阶段和执行证据。</p>' +
        '<div class="function-showcase-function-list">' +
          (functions.length ? functions.map(functionPointHtml).join("") : '<div class="function-showcase-empty compact">当前任务未上报功能点</div>') +
        '</div>' +
      '</section>' +
      '<div class="function-showcase-stage-list">' + models.map(stageHtml).join("") + '</div>';
  }

  function setContext(next) {
    next = next || {};
    var nextContext = {
      scenarioId: next.scenarioId || null,
      scenarioName: next.scenarioName || null,
      runId: next.runId || null,
    };
    var changed = String(context.runId || "") !== String(nextContext.runId || "") ||
      String(context.scenarioId || "") !== String(nextContext.scenarioId || "");
    var waiting = pendingViews.slice();
    context = nextContext;
    if (changed) {
      resetViews();
      waiting.forEach(upsertView);
    }
    requestRender();
  }

  function receiveView(view) {
    if (!view) {
      resetViews();
      requestRender();
      return;
    }
    if (!context.runId) {
      var pendingId = String(view.workflow_id || "");
      pendingViews = pendingViews.filter(function (item) { return String(item.workflow_id || "") !== pendingId; });
      pendingViews.push(view);
      requestRender();
      return;
    }
    if (upsertView(view)) requestRender();
  }

  function receiveHistory(payload) {
    if (!payload || !context.runId || String(payload.run_id || "") !== String(context.runId)) return;
    var historyViews = Array.isArray(payload.views) ? payload.views : [];
    var historyOrder = [];
    historyViews.forEach(function (view) {
      if (upsertView(view)) historyOrder.push(String(view.workflow_id));
    });
    viewOrder = historyOrder.concat(viewOrder.filter(function (id) { return historyOrder.indexOf(id) < 0; }));
    requestRender();
  }

  function init() {
    root = document.getElementById("function-showcase-root");
    if (!root) return;
    root.addEventListener("change", function (event) {
      if (event.target && event.target.id === "function-showcase-filter") {
        selectedFunctionId = event.target.value || "__all__";
        renderSuspended = false;
        requestRender();
      }
    });
    root.addEventListener("focusin", function (event) {
      if (event.target && event.target.id === "function-showcase-filter") renderSuspended = true;
    });
    root.addEventListener("focusout", function (event) {
      if (event.target && event.target.id === "function-showcase-filter") {
        renderSuspended = false;
        if (renderPending) requestRender();
      }
    });
    root.addEventListener("click", function (event) {
      var coverageButton = event.target.closest && event.target.closest("[data-showcase-coverage-function]");
      if (coverageButton) {
        selectedCoverageFunctionId = coverageButton.dataset.showcaseCoverageFunction || "__all__";
        requestRender();
        return;
      }
      var functionButton = event.target.closest && event.target.closest("[data-showcase-function]");
      if (functionButton) {
        selectedFunctionId = functionButton.dataset.showcaseFunction || "__all__";
        requestRender();
        return;
      }
      var button = event.target.closest && event.target.closest("[data-showcase-activity]");
      if (!button) return;
      document.dispatchEvent(new CustomEvent("amos:function-showcase-locate", {
        detail: {activity_id: button.dataset.showcaseActivity, workflow_id: button.dataset.showcaseWorkflow,
          workspace: "execution", activity_tab: "algorithm"},
      }));
    });
    document.addEventListener("amos:workflow-view", function (event) { receiveView(event.detail || null); });
    document.addEventListener("amos:workflow-view-history", function (event) { receiveHistory(event.detail || null); });
    if (typeof window.fetch === "function") {
      window.fetch("/api/v1/scenarios/coverage").then(function (response) {
        if (!response.ok) throw new Error("coverage request failed");
        return response.json();
      }).then(function (payload) {
        receiveCoverage(payload && payload.data ? payload.data : payload);
      }).catch(function () {
        coverageData = null;
      });
    }
    requestRender();
  }

  function receiveCoverage(payload) {
    if (!payload || !Array.isArray(payload.scenarios) || !Array.isArray(payload.items)) return;
    coverageData = payload;
    requestRender();
  }

  return {
    init: init,
    setContext: setContext,
    receiveView: receiveView,
    receiveHistory: receiveHistory,
    receiveCoverage: receiveCoverage,
    normalize: normalize,
    escapeHtml: escapeHtml,
  };
})();
