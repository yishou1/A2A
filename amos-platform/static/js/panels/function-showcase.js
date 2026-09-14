/* function-showcase.js — read-only projection of amos.workflow-view.v2 */

window.PlatformFunctionShowcase = (function () {
  "use strict";

  var root = null;
  var context = {scenarioId: null, scenarioName: null, runId: null};
  var viewsById = {};
  var viewOrder = [];
  var pendingViews = [];
  var selectedFunctionId = "__all__";
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

  function activityHtml(activity, model) {
    var pending = !activity.started;
    return '<article class="function-showcase-activity ' + escapeHtml(String(activity.status || "unknown")) + '">' +
      '<header><div><b>' + escapeHtml(activity.title) + '</b><small>' + escapeHtml(activity.id) + '</small></div>' +
        '<span>' + escapeHtml(statusLabel(activity.status)) + '</span></header>' +
      definitionRows([
        ["执行智能体", activity.agent], ["实例", activity.instanceId], ["执行模式", activity.executionMode],
      ]) +
      (pending ? '<p class="function-showcase-muted">活动尚未执行，不展示输入、结果或调用明细。</p>' :
        '<div class="function-showcase-io"><span>输入字段 <b>' + escapeHtml(valueOrUnknown(activity.inputFieldCount)) +
        '</b></span><span>结果字段 <b>' + escapeHtml(valueOrUnknown(activity.outputFieldCount)) + '</b></span></div>' +
        '<div class="function-showcase-algorithms">' +
          (activity.algorithms.length ? activity.algorithms.map(function (algorithm) {
            return algorithmHtml(algorithm, activity.calls);
          }).join("") : '<span class="function-showcase-muted">未上报算法元数据</span>') +
        '</div>' +
        (activity.derivedCallCount ? '<p class="function-showcase-derived">另有 ' + escapeHtml(activity.derivedCallCount) +
          ' 条推断或活动证据回填记录，未计入独立调用。</p>' : '') +
        '<div class="function-showcase-calls">' + activity.calls.map(function (call) { return callHtml(call, false); }).join("") + '</div>') +
      '<footer><button type="button" class="btn btn-sm" data-showcase-activity="' + escapeHtml(activity.id) +
        '" data-showcase-workflow="' + escapeHtml(model.workflowId) + '">在任务执行检查器中查看</button></footer>' +
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
            activityCount: 0, taskCount: 0,
          };
          order.push(item.id);
        }
        if (item.scope === "conditional") aggregate.scope = "conditional";
        aggregate.activityCount += item.activityIds.length;
        aggregate.taskCount += 1;
      });
    });
    return order.map(function (id) { return byId[id]; });
  }

  function functionPointHtml(item) {
    var scope = item.scope === "conditional" ? "条件功能" : "后端功能";
    return '<button type="button" class="function-showcase-function' +
      (item.id === selectedFunctionId ? ' selected' : '') + '" data-showcase-function="' + escapeHtml(item.id) + '">' +
      '<code>' + escapeHtml(item.id) + '</code><b>' + escapeHtml(item.name) + '</b><small>' +
      escapeHtml(scope + " · " + item.taskCount + " 个任务阶段 · " + item.activityCount + " 项活动关联") + '</small></button>';
  }

  function stageHtml(model, index) {
    var selected = model.functions.find(function (item) { return item.id === selectedFunctionId; });
    var visibleActivities = selected ? model.activities.filter(function (activity) {
      return selected.activityIds.indexOf(activity.id) >= 0;
    }) : (selectedFunctionId === "__all__" ? model.activities : []);
    if (selectedFunctionId !== "__all__" && !selected) return "";
    var stageLabel = model.checkpoint || "未上报检查点";
    return '<section class="function-showcase-card function-showcase-stage">' +
      '<div class="function-showcase-heading"><div><small>第 ' + escapeHtml(index + 1) + ' 个分析任务 · ' +
        escapeHtml(stageLabel) + '</small><h2>' + escapeHtml(model.workflowId || "未上报工作流 ID") + '</h2></div><span>' +
        escapeHtml(visibleActivities.length + " 项活动") + '</span></div>' +
      definitionRows([["检查点", model.checkpoint], ["实际工作流文件", model.workflowFile],
        ["独立调用", model.independentCallCount], ["成功结果", model.successfulResultCount]]) +
      '<div class="function-showcase-stage-activities"><div class="function-showcase-activity-list">' +
        (visibleActivities.length ? visibleActivities.map(function (activity) { return activityHtml(activity, model); }).join("") :
          '<div class="function-showcase-empty compact">后端未提供该功能在此任务阶段的关联活动</div>') +
      '</div></div>' +
      (model.taskCalls.length || model.taskDerivedCallCount ? '<div class="function-showcase-task-calls"><h3>任务级调用记录 <small>活动归属未上报 · ' +
        escapeHtml(model.taskCalls.length + " 条") + '</small></h3>' + model.taskCalls.map(function (call) { return callHtml(call, true); }).join("") +
        (model.taskDerivedCallCount ? '<p class="function-showcase-derived">另有 ' + escapeHtml(model.taskDerivedCallCount) +
          ' 条任务级推断或活动证据回填记录，未计入独立调用。</p>' : '') + '</div>' : '') +
    '</section>';
  }

  function render() {
    if (!root) return;
    var models = modelsForContext();
    if (!models.length) {
      root.innerHTML = '<div class="function-showcase-empty">当前运行尚未收到可展示的工作流记录</div>';
      return;
    }
    var functions = aggregateFunctions(models);
    if (selectedFunctionId !== "__all__" && !functions.some(function (item) { return item.id === selectedFunctionId; })) {
      selectedFunctionId = "__all__";
    }
    var activityCount = models.reduce(function (total, model) { return total + model.activities.length; }, 0);
    var independentCallCount = models.reduce(function (total, model) { return total + model.independentCallCount; }, 0);
    var instanceRecords = [];
    models.forEach(function (model) {
      model.instances.forEach(function (instance) {
        instanceRecords.push({instance: instance, workflowId: model.workflowId, checkpoint: model.checkpoint});
      });
    });
    var consoleLink = document.getElementById("function-showcase-algorithm-console");
    var consoleHref = consoleLink && consoleLink.getAttribute ? consoleLink.getAttribute("href") : "";
    root.innerHTML =
      '<p class="function-showcase-scope">本页累计展示当前场景、当前运行内已经收到的分析任务。功能关联不代表功能完成，算法返回结果也不代表业务功能完成；这些记录不等于整场剧本已完整执行。</p>' +
      '<section class="function-showcase-card"><div class="function-showcase-heading"><div><small>当前剧本运行</small><h2>' +
        escapeHtml(context.scenarioName || context.scenarioId || "未上报场景") + '</h2></div><span>只读累计</span></div>' +
        definitionRows([["场景 ID", context.scenarioId], ["运行 ID", context.runId], ["已接收分析任务", models.length],
          ["最新检查点", models[models.length - 1].checkpoint]]) +
      '</section>' +
      '<div class="function-showcase-metrics"><span><small>分析任务</small><b>' + models.length + '</b></span>' +
        '<span><small>功能点</small><b>' + functions.length + '</b></span>' +
        '<span><small>活动记录</small><b>' + activityCount + '</b></span>' +
        '<span><small>独立调用</small><b>' + independentCallCount + '</b></span></div>' +
      '<section class="function-showcase-card"><div class="function-showcase-heading"><div><small>后端活动关联</small><h2>功能关联</h2></div></div>' +
        '<label class="function-showcase-filter">查看功能<select id="function-showcase-filter"><option value="__all__">全部功能与活动</option>' +
          functions.map(function (item) {
            var suffix = item.scope === "conditional" ? "（条件功能）" : "";
            return '<option value="' + escapeHtml(item.id) + '"' + (item.id === selectedFunctionId ? " selected" : "") + '>' +
              escapeHtml(item.id + " · " + item.name + suffix + " · " + item.taskCount + " 个任务阶段") + '</option>';
          }).join("") + '</select></label>' +
        '<p class="function-showcase-muted">下列功能点按本轮已收到的任务持续保留。关联仅来自后端活动标识和 activity: 引用；未重新建立功能与算法映射。</p>' +
        '<div class="function-showcase-function-list">' +
          (functions.length ? functions.map(functionPointHtml).join("") : '<div class="function-showcase-empty compact">当前任务未上报功能点</div>') +
        '</div>' +
      '</section>' +
      '<div class="function-showcase-stage-list">' + models.map(stageHtml).join("") + '</div>' +
      '<section class="function-showcase-card function-showcase-management"><div><small>沿用现有管理系统</small><h2>算法管理</h2><p>查看算法包、运行状态和管理能力。</p></div>' +
        (consoleHref ? '<a class="btn btn-sm" href="' + escapeHtml(consoleHref) + '" target="_blank" rel="noopener noreferrer">打开算法管理台 ↗</a>' : '<span>入口未配置</span>') + '</section>' +
      '<section class="function-showcase-card"><div class="function-showcase-heading"><div><small>当前运行实际上报</small><h2>智能体记录</h2></div><span>' +
        escapeHtml(instanceRecords.length + " 条实例记录") + '</span></div><p class="function-showcase-muted">实例记录按任务阶段展示，数量不能证明完整协同过程；同一实例在不同任务中的上报会分别保留。</p><div class="function-showcase-instance-list">' +
        (instanceRecords.length ? instanceRecords.map(instanceHtml).join("") : '<div class="function-showcase-empty compact">当前运行尚未上报智能体实例</div>') + '</div></section>';
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
    requestRender();
  }

  return {
    init: init,
    setContext: setContext,
    receiveView: receiveView,
    receiveHistory: receiveHistory,
    normalize: normalize,
    escapeHtml: escapeHtml,
  };
})();
