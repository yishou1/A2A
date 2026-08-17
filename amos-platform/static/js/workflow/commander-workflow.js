/* commander-workflow.js — AMOS workflow-view v2 renderer and controller */

window.PlatformWorkflow = (function () {
  var API = null;
  var options = {};
  var pollTimer = null;
  var healthTimer = null;
  var workflowId = null;
  var activeWorkflowId = null;
  var workflowRunId = null;
  var simulationRunId = null;
  var viewGeneration = 0;
  var runHistoryGeneration = 0;
  var lastView = null;
  var lastViewSignature = null;
  var runManifest = null;
  var historyRunId = null;
  var historyLoadingRunId = null;
  var viewCache = {};
  var selectedActivityId = null;
  var activityTab = "input";
  var followCurrentActivity = true;
  var activeAnalysisSignature = null;
  var submitting = false;
  var notifiedTerminalKey = null;
  var workflowTasks = [
    {checkpoint:"MAR-CP-PERCEPTION", label:"观察与识别", phase:"Observe · Find/Fix"},
    {checkpoint:"MAR-CP-ASSESS", label:"航迹评估", phase:"Orient · Track"},
    {checkpoint:"MAR-CP-PLAN", label:"方案决策", phase:"Decide · Target"},
    {checkpoint:"MAR-CP-CLOSE", label:"执行与复核", phase:"Act · Engage/Assess"},
  ];

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function statusLabel(state) {
    return ({queued:"等待执行",pending:"待执行",running:"执行中",executing:"执行中",completed:"已完成",verified:"已验证",declared:"已声明",failed:"失败",error:"错误",cancelled:"已取消",unknown:"未上报",unavailable:"不可用",checkpoint_only:"可恢复",connected:"在线",degraded:"依赖降级",diagnostic:"直连诊断",offline:"离线",submitting:"提交中",stale_run:"上一轮结果"})[state] || state || "尚未开始";
  }

  function errorMessage(value) {
    if (!value) return "连接失败";
    if (typeof value === "string") return value;
    return value.message || value.code || JSON.stringify(value);
  }

  function formatSimTime(value) {
    var total = Math.max(0, Math.floor(Number(value) || 0));
    return "T+" + String(Math.floor(total / 3600)).padStart(2, "0") + ":" +
      String(Math.floor((total % 3600) / 60)).padStart(2, "0") + ":" +
      String(total % 60).padStart(2, "0");
  }

  function modeLabel(mode) {
    return ({
      remote_agent: "远程 Agent",
      remote: "远程服务",
      builtin: "内置执行",
      demo_adapter: "演示适配器",
      simulated_adapter: "模拟适配器",
      simulation_executor: "模拟执行器",
      derived_from_tracking: "后端派生",
      branch_builtin_algorithm: "内置算法",
      branch_builtin_algorithm_simulation_only: "仿真内置算法",
      branch_pipeline_mock_driven: "Mock 驱动",
      stub: "Stub",
      mock: "Mock",
      unspecified: "未上报执行模式",
    })[mode] || mode || "未上报执行模式";
  }

  function setBadge(element, state) {
    if (!element) return;
    element.textContent = statusLabel(state);
    element.className = "badge";
    if (state === "completed" || state === "connected") element.classList.add("badge-green");
    else if (state === "running" || state === "queued" || state === "submitting") element.classList.add("badge-cyan");
    else if (state === "degraded" || state === "diagnostic" || state === "checkpoint_only" || state === "stale_run") element.classList.add("badge-yellow");
    else if (state === "failed" || state === "error" || state === "unavailable" || state === "offline") element.classList.add("badge-red");
  }

  function selectTab(name, notifyWorkspace) {
    document.querySelectorAll("[data-workflow-tab]").forEach(function (button) {
      button.classList.toggle("active", button.dataset.workflowTab === name);
    });
    document.querySelectorAll("[data-workflow-panel]").forEach(function (panel) {
      panel.hidden = panel.dataset.workflowPanel !== name;
    });
    if (notifyWorkspace !== false) {
      document.dispatchEvent(new CustomEvent("amos:workflow-section", {detail: name}));
    }
  }

  function activityKey(item) {
    return String(item && (item.activity_id || item.work_item || ("activity-" + item.index)) || "");
  }

  function workflowCheckpoint(submission) {
    var stage = submission && submission.stage_transfer || {};
    return stage.checkpoint_id || submission && submission.checkpoint_id || null;
  }

  function formatDuration(value) {
    if (value == null || value === "") return null;
    var duration = Number(value);
    if (!Number.isFinite(duration)) return null;
    return duration >= 1000 ? (duration / 1000).toFixed(duration >= 10000 ? 0 : 1) + "s" : Math.round(duration) + "ms";
  }

  function renderTaskHistory() {
    var root = document.getElementById("wf-task-history");
    if (!root) return;
    var ids = (runManifest && runManifest.workflow_ids || []).map(String);
    [activeWorkflowId, workflowId].forEach(function (id) {
      if (id && ids.indexOf(String(id)) < 0) ids.push(String(id));
    });
    var submissions = {};
    (runManifest && runManifest.submissions || []).forEach(function (row) {
      if (row && row.workflow_id) submissions[String(row.workflow_id)] = row.snapshot || {};
    });
    var archived = {};
    (runManifest && runManifest.workflow_views || []).forEach(function (row) {
      if (row && row.workflow_id) archived[String(row.workflow_id)] = row;
    });
    var slots = workflowTasks.map(function (task) { return {task:task, id:null}; });
    ids.forEach(function (id) {
      var live = viewCache[id] || {};
      var checkpoint = workflowCheckpoint(submissions[id] || live.submission || {});
      var slot = slots.find(function (item) { return item.task.checkpoint === checkpoint; });
      if (!slot) slot = slots.find(function (item) { return !item.id; });
      if (slot && !slot.id) slot.id = id;
    });
    root.innerHTML = slots.map(function (slot) {
      var id = slot.id;
      if (!id) {
        return '<button type="button" disabled><small>' + escapeHtml(slot.task.phase) + '</small><b>' +
          escapeHtml(slot.task.label) + '</b><span>未生成任务</span></button>';
      }
      var view = viewCache[id] || {};
      var record = archived[id] || {};
      var state = view.status || record.status || "unknown";
      var counts = view.orchestration && view.orchestration.counts || {};
      var activityCount = counts.total;
      if (activityCount == null && record.result && Array.isArray(record.result.cards)) activityCount = record.result.cards.length;
      var duration = formatDuration(view.metrics && view.metrics.workflow_duration_ms || record.metrics && record.metrics.workflow_duration_ms);
      var meta = [statusLabel(state)];
      if (activityCount != null) meta.push(activityCount + " 项活动");
      if (duration) meta.push(duration);
      var classes = [state];
      if (String(id) === String(workflowId || "")) classes.push("selected");
      if (String(id) === String(activeWorkflowId || "")) classes.push("current");
      return '<button type="button" class="' + escapeHtml(classes.join(" ")) + '" data-workflow-history="' +
        escapeHtml(id) + '" aria-pressed="' + (String(id) === String(workflowId || "")) + '"><small>' +
        escapeHtml(slot.task.phase) + '</small><b>' + escapeHtml(slot.task.label) + '</b><span>' +
        escapeHtml(meta.join(" · ")) + '</span></button>';
    }).join("");
  }

  async function refreshRunHistory(force) {
    if (!API || !API.loadRun || !simulationRunId) return null;
    if (!force && historyRunId === simulationRunId && runManifest) return runManifest;
    if (!force && historyLoadingRunId === simulationRunId) return null;
    var requestedRunId = simulationRunId;
    var generation = ++runHistoryGeneration;
    historyLoadingRunId = requestedRunId;
    try {
      var manifest = await API.loadRun(requestedRunId);
      if (generation !== runHistoryGeneration || requestedRunId !== simulationRunId) return null;
      runManifest = manifest || {};
      historyRunId = requestedRunId;
      renderTaskHistory();
      return runManifest;
    } catch (error) {
      if (generation === runHistoryGeneration) {
        runManifest = error && error.status === 404 ? {} : null;
        historyRunId = requestedRunId;
        renderTaskHistory();
      }
      return null;
    } finally {
      if (generation === runHistoryGeneration) historyLoadingRunId = null;
    }
  }

  function renderSubmission(submission) {
    var root = document.getElementById("wf-input-snapshot");
    if (!root) return;
    if (!submission || !Object.keys(submission).length) {
      root.innerHTML = '<div class="workflow-empty">流程尚未产生后端输入快照</div>';
      return;
    }
    var counts = submission.counts || {};
    var rows = submission.attachments || [];
    var gateway = submission.transport === "gateway";
    var packageInfo = submission.package || {};
    var packageText = packageInfo.package_id
      ? " · PACKAGE " + (packageInfo.verified ? "校验通过" : "校验未确认")
      : "";
    var stage = submission.stage_transfer || {};
    var inputLabels = {
      sensor_observations:"传感器观测", sensor_geometry:"采集几何", fused_tracks:"融合航迹",
      track_history:"航迹历史", current_media:"本阶段资料", environment:"环境",
      network_state:"通信状态", asset_readiness:"平台可用性", operational_constraints:"任务约束",
      task_state:"任务状态", verified_workflow_output:"已验证工作流输出",
      execution_provenance:"执行来源", baseline_and_current_media:"基线与当前资料"
    };
    var outputLabels = {
      detections:"检测结果", source_evidence_refs:"来源引用", contact_associations:"接触关联",
      location_confidence:"定位置信度", classification_candidates:"分类候选",
      maintained_tracks:"持续航迹", risk_assessment:"风险评估", evidence_gaps:"证据缺口",
      resource_allocation:"资源分配", candidate_plans:"候选方案", constraint_review:"约束审查",
      approved_task_state:"批准任务状态", execution_monitoring_refs:"执行监测引用",
      effect_assessment:"效果评估", completion_score:"完成度", replan_recommendation:"重规划建议"
    };
    var inputRows = stage.required_inputs || [];
    var stageBlock = Object.keys(stage).length ?
      '<div class="workflow-stage-transfer">' +
        '<div class="workflow-stage-head"><b>' + escapeHtml(stage.checkpoint_id || "手动快照") + '</b>' +
          '<span>' + escapeHtml(stage.phase || "—") + ' · ' + escapeHtml(
            formatSimTime(stage.window && stage.window.start_sec)
          ) + '–' + escapeHtml(formatSimTime(stage.window && stage.window.end_sec)) + '</span></div>' +
        '<div class="workflow-stage-groups">' + inputRows.map(function (row) {
          return '<span class="' + (row.status === "available" ? "available" : "missing") + '">' +
            escapeHtml(inputLabels[row.data_group] || row.data_group) + '<b>' + escapeHtml(row.count == null ? "—" : row.count) + '</b></span>';
        }).join("") + '</div>' +
        '<dl class="workflow-stage-scope">' +
          '<div><dt>增量资料</dt><dd>' + escapeHtml((stage.incremental_media_ids || []).join(", ") || "无") + '</dd></div>' +
          '<div><dt>关联基线</dt><dd>' + escapeHtml((stage.context_media_ids || []).join(", ") || "无") + '</dd></div>' +
          '<div><dt>调用范围</dt><dd>' + escapeHtml((stage.requested_agent_ids || []).join(", ") || "未声明") +
            ' · ' + escapeHtml((stage.requested_algorithm_ids || []).join(", ") || "未声明") + '</dd></div>' +
          '<div><dt>预期输出</dt><dd>' + escapeHtml((stage.expected_outputs || []).map(function (key) { return outputLabels[key] || key; }).join("、") || "未声明") + '</dd></div>' +
        '</dl>' +
      '</div>' : '';
    root.innerHTML =
      '<div class="workflow-snapshot-head">' +
        '<span><small>仿真时刻</small><b>' + escapeHtml(formatSimTime(submission.simulation_time_sec)) + '</b></span>' +
        '<span><small>稳定航迹</small><b>' + escapeHtml(counts.contacts || 0) + '</b></span>' +
        '<span><small>' + (gateway ? "当前观测" : "原始观测") + '</small><b>' + escapeHtml(counts.observations || 0) + '</b></span>' +
        '<span><small>' + (gateway ? "连续事件" : "历史帧") + '</small><b>' + escapeHtml(gateway ? (counts.events || 0) : (counts.perception_frames || 0)) + '</b></span>' +
        '<span><small>己方平台</small><b>' + escapeHtml(counts.friendly_platforms || 0) + '</b></span>' +
        '<span><small>' + (gateway ? "媒体引用" : "媒体附件") + '</small><b>' + escapeHtml(counts.attachments || 0) + '</b></span>' +
      '</div>' +
      '<div class="workflow-contract"><span class="workflow-acceptance ' + (submission.accepted ? "accepted" : "rejected") + '">' +
        (submission.accepted ? "已接收" : "未接收") + '</span>' + escapeHtml(String(submission.transport || "backend").toUpperCase()) +
        ' · ' + escapeHtml(submission.contract || "—") + escapeHtml(packageText) + '</div>' +
      '<div class="workflow-objective">' + escapeHtml(submission.objective || "未提供分析目标") + '</div>' +
      stageBlock +
      '<div class="workflow-attachment-list">' + (rows.length ? rows.map(function (item) {
        var checksum = (item.checksum || {}).value || "";
        return '<div class="workflow-attachment">' +
          '<span class="workflow-attachment-kind">' + escapeHtml((item.modality || item.kind || "DATA").toUpperCase()) + '</span>' +
          '<span><b>' + escapeHtml(item.name || item.id) + '</b><small>' + escapeHtml(item.sensor_id || "unknown sensor") + (item.captured_at_sim_time == null ? "" : ' · ' + escapeHtml(formatSimTime(item.captured_at_sim_time))) + '</small></span>' +
          '<span class="workflow-checksum" title="' + escapeHtml(checksum) + '">SHA256 ' + escapeHtml(checksum.slice(0, 8) || "—") + '</span>' +
        '</div>';
      }).join("") : '<div class="workflow-empty">无可提交媒体</div>') + '</div>';
  }

  function factList(value) {
    if (value == null || value === "" || Array.isArray(value) && !value.length) {
      return '<div class="workflow-detail-empty">后端未上报</div>';
    }
    if (!Array.isArray(value)) {
      return '<div class="workflow-detail-value">' + escapeHtml(value) + '</div>';
    }
    return '<dl class="workflow-detail-facts">' + value.map(function (item) {
      if (!item || typeof item !== "object") return '<div><dt>值</dt><dd>' + escapeHtml(item) + '</dd></div>';
      return '<div><dt>' + escapeHtml(item.key || "指标") + '</dt><dd>' + escapeHtml(item.value == null ? "后端未上报" : item.value) + '</dd></div>';
    }).join("") + '</dl>';
  }

  function detailDefinitionList(rows) {
    return '<dl class="workflow-detail-definition">' + rows.map(function (row) {
      return '<div><dt>' + escapeHtml(row[0]) + '</dt><dd>' + escapeHtml(provided(row[1])) + '</dd></div>';
    }).join("") + '</dl>';
  }

  function renderActivityDetail(view) {
    var root = document.getElementById("wf-activity-detail");
    var title = document.getElementById("wf-detail-title");
    var badge = document.getElementById("wf-detail-status");
    if (!root || !title || !badge) return;
    var rows = view && view.orchestration && view.orchestration.activities || [];
    var activity = rows.find(function (row) { return activityKey(row) === selectedActivityId; });
    var details = view && view.activity_details || {};
    var detail = activity && details[activityKey(activity)];
    document.querySelectorAll("[data-activity-tab]").forEach(function (button) {
      var selected = button.dataset.activityTab === activityTab;
      button.classList.toggle("active", selected);
      button.setAttribute("aria-selected", String(selected));
    });
    if (!activity) {
      title.textContent = "请选择活动";
      badge.textContent = "未选择";
      badge.className = "status-chip neutral";
      root.innerHTML = '<div class="workflow-detail-empty">尚无活动详情</div>';
      return;
    }
    var sequenceContainer = activity.type === "sequence" || /activatity-\d+-sequence$/i.test(activity.activity_id || "");
    title.textContent = sequenceContainer ? "顺序流程容器" : (activity.role || activity.work_item || activity.activity_id || "执行项");
    badge.textContent = statusLabel(activity.status);
    badge.className = "status-chip " + (activity.status === "completed" ? "success" : activity.status === "running" ? "info" : activity.status === "failed" ? "danger" : "neutral");
    if (!detail) {
      root.innerHTML = '<div class="workflow-detail-empty">后端未上报该活动的详情记录</div>';
      return;
    }
    if (activityTab === "input") {
      root.innerHTML = '<div class="workflow-detail-section"><h4>活动输入摘要</h4>' + factList(detail.input_summary) + '</div>';
      return;
    }
    if (activityTab === "output") {
      root.innerHTML = '<div class="workflow-detail-section"><h4>活动输出摘要</h4>' + factList(detail.output_summary) + '</div>';
      return;
    }
    if (activityTab === "call") {
      var call = detail.agent_call || {};
      root.innerHTML = '<div class="workflow-detail-section"><h4>Agent 调用</h4>' + detailDefinitionList([
        ["角色", call.role], ["Agent", call.agent], ["实例 ID", call.instance_id],
        ["执行模式", call.execution_mode ? modeLabel(call.execution_mode) : null],
        ["耗时", formatDuration(call.duration_ms)], ["重试次数", call.retry_count],
        ["最近心跳", call.last_heartbeat], ["开始时间", detail.started_at], ["结束时间", detail.finished_at],
      ]) + '</div>';
      return;
    }
    if (activityTab === "algorithm") {
      var algorithms = detail.algorithms || [];
      root.innerHTML = algorithms.length ? '<div class="workflow-detail-algorithms">' + algorithms.map(function (row) {
        return '<article><header><b>' + escapeHtml(row.name || row.algorithm_id) + '</b><span>' + escapeHtml(statusLabel(row.status)) + '</span></header>' +
          detailDefinitionList([["算法 ID", row.algorithm_id], ["模型", row.model_id], ["版本", row.version],
            ["执行 Agent", row.agent], ["执行模式", row.execution_mode ? modeLabel(row.execution_mode) : null],
            ["耗时", formatDuration(row.duration_ms)]]) + '</article>';
      }).join("") + '</div>' : '<div class="workflow-detail-empty">后端未上报该活动的算法调用</div>';
      return;
    }
    var traceEvents = detail.trace_events || [];
    root.innerHTML = '<div class="workflow-detail-section"><h4>依赖与证据</h4>' + detailDefinitionList([
      ["活动 ID", detail.activity_id], ["工作项", detail.work_item],
      ["显式依赖", (detail.depends_on || []).join(", ") || null],
      ["证据引用", (detail.evidence_refs || []).join(", ") || null],
    ]) + '</div><div class="workflow-detail-section"><h4>关联执行事件</h4>' +
      (traceEvents.length ? '<div class="workflow-detail-events">' + traceEvents.map(function (event) {
        return '<div><time>' + escapeHtml(event.timestamp || "后端未上报") + '</time><b>' + escapeHtml(event.event) +
          '</b><span>' + escapeHtml([event.agent, event.message].filter(Boolean).join(" · ") || "无附加消息") + '</span></div>';
      }).join("") + '</div>' : '<div class="workflow-detail-empty">后端未上报该活动的执行事件</div>') + '</div>';
  }

  function renderActivities(view) {
    var root = document.getElementById("wf-activity-list");
    var traceRoot = document.getElementById("wf-trace-list");
    if (!root || !traceRoot) return;
    var orchestration = view && view.orchestration || {};
    var rows = orchestration.activities || [];
    var currentActivity = view && view.current_activity;
    var explicitCurrent = currentActivity && typeof currentActivity === "object"
      ? (currentActivity.activity_id || currentActivity.work_item) : currentActivity;
    if (followCurrentActivity && rows.length) {
      var current = rows.find(function (item) {
        return explicitCurrent && [item.activity_id, item.work_item].map(String).indexOf(String(explicitCurrent)) >= 0;
      }) || rows.find(function (item) { return item.status === "running"; }) || rows[rows.length - 1];
      selectedActivityId = activityKey(current);
    } else if (!rows.some(function (item) { return activityKey(item) === selectedActivityId; })) {
      selectedActivityId = rows.length ? activityKey(rows[0]) : null;
    }
    var count = document.getElementById("wf-activity-count");
    if (count) count.textContent = rows.length + " 项";
    root.innerHTML = rows.length ? rows.map(function (item) {
      var state = item.status || "pending";
      var sequenceContainer = item.type === "sequence" || /activatity-\d+-sequence$/i.test(item.activity_id || "");
      var title = sequenceContainer ? "顺序流程容器" : (item.role || item.work_item || item.activity_id || "执行项");
      var executor = sequenceContainer
        ? "Commander 流程控制 · " + (item.activity_id || item.work_item || "内部节点")
        : (item.agent || "Agent 未分配");
      var dependency = (item.depends_on || []).length ? " · 依赖 " + item.depends_on.join(", ") : "";
      return '<button type="button" class="workflow-activity ' + escapeHtml(state) + (activityKey(item) === selectedActivityId ? " selected" : "") +
        '" data-activity-id="' + escapeHtml(activityKey(item)) + '" aria-pressed="' + (activityKey(item) === selectedActivityId) + '">' +
        '<span class="workflow-step-index">' + String(item.index || 0).padStart(2, "0") + '</span>' +
        '<span class="workflow-activity-body"><b>' + escapeHtml(title) + '</b>' +
        '<small>' + escapeHtml(executor) + (item.duration_ms == null ? "" : " · " + escapeHtml(formatDuration(item.duration_ms))) + escapeHtml(dependency) + '</small>' +
        (item.error ? '<em>' + escapeHtml(item.error) + '</em>' : '') + '</span>' +
        '<span class="workflow-mode-tag">' + escapeHtml(modeLabel(item.execution_mode)) + '</span>' +
        '<span class="workflow-state-tag">' + escapeHtml(statusLabel(state)) + '</span>' +
      '</button>';
    }).join("") : '<div class="workflow-empty">未返回执行计划</div>';

    var trace = (orchestration || {}).trace || [];
    traceRoot.innerHTML = trace.length ? trace.slice().reverse().map(function (item) {
      return '<div class="workflow-trace-row"><span>' + escapeHtml(item.timestamp || "—") + '</span><b>' + escapeHtml(item.event) + '</b><small>' +
        escapeHtml([item.role, item.agent, item.message].filter(Boolean).join(" · ")) + '</small></div>';
    }).join("") : '<div class="workflow-empty">未返回执行事件</div>';
    renderActivityDetail(view);
  }

  function renderResults(result) {
    var warnings = document.getElementById("wf-warning-list");
    var cards = document.getElementById("wf-result-cards");
    var associationRoot = document.getElementById("wf-association-list");
    var analysisRoot = document.getElementById("wf-analysis-summary");
    if (!warnings || !cards || !associationRoot || !analysisRoot) return;
    var warningRows = (result || {}).warnings || [];
    warnings.hidden = !warningRows.length;
    warnings.innerHTML = warningRows.map(function (item) { return '<div><b>警告</b> ' + escapeHtml(item) + '</div>'; }).join("");

    var cardRows = (result || {}).cards || [];
    cards.innerHTML = cardRows.length ? cardRows.map(function (card) {
      var facts = card.facts || [];
      return '<article class="workflow-result-card ' + escapeHtml(card.status || "pending") + '">' +
        '<header><b>' + escapeHtml(card.role || card.work_item || card.activity_id || "分项结果") + '</b><span>' + escapeHtml(modeLabel(card.execution_mode)) + '</span></header>' +
        '<small>' + escapeHtml(card.agent || "Agent 未标注") + ' · ' + escapeHtml(statusLabel(card.status)) + '</small>' +
        '<div class="workflow-facts">' + (facts.length ? facts.map(function (fact) {
          return '<span><small>' + escapeHtml(fact.key) + '</small><b>' + escapeHtml(fact.value) + '</b></span>';
        }).join("") : '<em>未返回结构化指标</em>') + '</div>' +
        (card.error ? '<div class="workflow-card-error">' + escapeHtml(card.error) + '</div>' : '') +
      '</article>';
    }).join("") : '<div class="workflow-empty">未返回分项结果</div>';

    var analysis = (result || {}).analysis || {};
    var summary = analysis.summary || {};
    var ranking = analysis.attention_ranking || [];
    analysisRoot.innerHTML = Object.keys(analysis).length ?
      '<div class="workflow-analysis-metrics">' +
        '<span><small>返回航迹</small><b>' + escapeHtml(summary.track_count != null ? summary.track_count : (analysis.tracks || []).length) + '</b></span>' +
        '<span><small>风险排序</small><b>' + escapeHtml(ranking.length) + '</b></span>' +
        '<span><small>平台影响</small><b>' + escapeHtml(summary.asset_impact_count != null ? summary.asset_impact_count : (analysis.asset_impacts || []).length) + '</b></span>' +
        '<span><small>已应用</small><b>' + escapeHtml((result || {}).applied_count || 0) + '</b></span>' +
      '</div>' +
      (ranking.length ? '<div class="workflow-ranking">' + ranking.slice(0, 8).map(function (item, index) {
        return '<div><span>' + escapeHtml(index + 1) + '</span><b>' + escapeHtml(item.track_id || item.entity_id || "未标识航迹") + '</b>' +
          '<small>' + escapeHtml(item.level || "unknown") + (item.score == null ? "" : " · " + escapeHtml(Number(item.score).toFixed(2))) + '</small></div>';
      }).join("") + '</div>' : '') :
      '<div class="workflow-empty">未返回分析结果</div>';
    var associations = analysis.associations || [];
    associationRoot.innerHTML = associations.length ? associations.map(function (item) {
      return '<div class="workflow-association ' + escapeHtml(item.status || "unmatched") + '">' +
        '<b>' + escapeHtml(item.backend_track_id || "未标识后端航迹") + '</b><span>→</span><b>' + escapeHtml(item.amos_track_id || "未关联") + '</b>' +
        '<small>' + escapeHtml(item.method || "unknown") + (item.distance_nm == null ? "" : " · " + escapeHtml(item.distance_nm) + " NM") + '</small>' +
      '</div>';
    }).join("") : '<div class="workflow-empty">未返回航迹关联</div>';
  }

  function provided(value, formatter) {
    if (value == null || value === "") return "未上报";
    return formatter ? formatter(value) : String(value);
  }

  function compactSummary(value) {
    if (value == null || value === "") return "未上报";
    if (Array.isArray(value)) {
      return value.map(function (item) {
        return item && typeof item === "object" ? String(item.key || "指标") + "=" + String(item.value == null ? "—" : item.value) : String(item);
      }).join("；") || "未上报";
    }
    if (typeof value === "object") {
      return Object.keys(value).slice(0, 5).map(function (key) { return key + "=" + String(value[key]); }).join("；") || "未上报";
    }
    return String(value);
  }

  function renderAgentTopology(agents) {
    var root = document.getElementById("wf-agent-topology");
    if (!root) return;
    agents = agents || {};
    var counts = agents.counts || {};
    var roles = agents.roles || [];
    var instances = agents.instances || [];
    root.innerHTML =
      '<div class="workflow-v2-summary">' +
        '<span><small>工作流活动</small><b>' + escapeHtml(provided(counts.workflow_activity_count)) + '</b></span>' +
        '<span><small>计划角色</small><b>' + escapeHtml(provided(counts.planned_role_count)) + '</b></span>' +
        '<span><small>Agent 类型</small><b>' + escapeHtml(provided(counts.role_count)) + '</b></span>' +
        '<span><small>已标识实例</small><b>' + escapeHtml(provided(counts.instance_count)) + '</b></span>' +
        '<span><small>真实实例</small><b>' + escapeHtml(provided(counts.real_instance_count)) + '</b></span>' +
      '</div>' +
      '<div class="workflow-agent-role-list">' + (roles.length ? roles.map(function (row) {
        return '<div class="workflow-agent-role ' + escapeHtml(row.status || "unknown") + '">' +
          '<b>' + escapeHtml(row.role) + '</b><small>' + escapeHtml(statusLabel(row.status)) +
          ' · 活动 ' + escapeHtml(row.activity_count || 0) + ' · 调用事件 ' + escapeHtml(row.call_count || 0) + '</small></div>';
      }).join("") : '<div class="workflow-empty">后端未返回 Agent 角色</div>') + '</div>' +
      '<div class="workflow-agent-instance-list">' + (instances.length ? instances.map(function (row) {
        var nature = row.is_stub ? "Stub" : (row.is_mock ? "Mock" : "真实服务");
        return '<article class="workflow-agent-instance ' + (row.real_service ? "real" : "non-real") + '">' +
          '<header><b>' + escapeHtml(row.agent || row.role || row.instance_id) + '</b><span>' + escapeHtml(nature) + '</span></header>' +
          '<small>实例 ' + escapeHtml(row.instance_id) + ' · ' + escapeHtml(statusLabel(row.status)) + '</small>' +
          '<dl><dt>执行模式</dt><dd>' + escapeHtml(modeLabel(row.execution_mode)) + '</dd>' +
          '<dt>活动 / 调用</dt><dd>' + escapeHtml(row.activity_count || 0) + ' / ' + escapeHtml(row.call_count || 0) + '</dd>' +
          '<dt>累计耗时</dt><dd>' + escapeHtml(provided(row.duration_ms, function (value) { return Math.round(Number(value)) + " ms"; })) + '</dd>' +
          '<dt>重试</dt><dd>' + escapeHtml(provided(row.retry_count)) + '</dd>' +
          '<dt>最近心跳</dt><dd>' + escapeHtml(provided(row.last_heartbeat)) + '</dd></dl>' +
        '</article>';
      }).join("") : '<div class="workflow-empty">后端未上报 Agent 实例标识</div>') + '</div>';
  }

  function renderAlgorithmCoverage(algorithms) {
    var root = document.getElementById("wf-algorithm-coverage");
    if (!root) return;
    algorithms = algorithms || {};
    var rows = (algorithms.items || []).filter(function (row) {
      return row.execution_status || row.status === "executing" || row.status === "verified";
    });
    var executingCount = rows.filter(function (row) { return row.status === "executing"; }).length;
    var verifiedCount = rows.filter(function (row) { return row.status === "verified"; }).length;
    root.innerHTML =
      '<div class="workflow-v2-summary">' +
        '<span><small>后端已调用</small><b>' + escapeHtml(rows.length) + '</b></span>' +
        '<span><small>执行中</small><b>' + escapeHtml(executingCount) + '</b></span>' +
        '<span><small>执行成功</small><b>' + escapeHtml(verifiedCount) + '</b></span>' +
      '</div>' +
      '<div class="workflow-algorithm-list">' + (rows.length ? rows.map(function (row) {
        return '<article class="workflow-algorithm ' + escapeHtml(row.status || "declared") + '">' +
          '<header><b>' + escapeHtml((row.requirement_id ? row.requirement_id + " · " : "") + (row.name || row.algorithm_id)) + '</b><span>' + escapeHtml(statusLabel(row.status)) + '</span></header>' +
          '<small>' + escapeHtml(row.tier === "engineering" ? "工程模型" : "核心算法") + ' · 执行 Agent ' + escapeHtml(provided(row.agent)) + '</small>' +
          '<dl><dt>算法 / 模型</dt><dd>' + escapeHtml(row.algorithm_id) + ' / ' + escapeHtml(provided(row.model_id)) + '</dd>' +
          '<dt>负责 Agent</dt><dd>' + escapeHtml(compactSummary(row.assigned_agents)) + '</dd>' +
          '<dt>版本</dt><dd>' + escapeHtml(provided(row.version)) + '</dd>' +
          '<dt>执行模式</dt><dd>' + escapeHtml(provided(row.execution_mode, modeLabel)) + '</dd>' +
          '<dt>耗时</dt><dd>' + escapeHtml(provided(row.duration_ms, function (value) { return Math.round(Number(value)) + " ms"; })) + '</dd>' +
          '<dt>输入摘要</dt><dd>' + escapeHtml(compactSummary(row.input_summary)) + '</dd>' +
          '<dt>结果摘要</dt><dd>' + escapeHtml(compactSummary(row.result_summary)) + '</dd>' +
          '<dt>Params / FLOPs</dt><dd>' + escapeHtml(provided(row.params)) + ' / ' + escapeHtml(provided(row.flops)) + '</dd></dl>' +
          (row.evidence_refs && row.evidence_refs.length ? '<footer>证据 ' + escapeHtml(row.evidence_refs.join(", ")) + '</footer>' : '') +
        '</article>';
      }).join("") : '<div class="workflow-empty">本次后端工作流尚未调用算法</div>') + '</div>';
  }

  function renderFunctionPoints(functionPoints) {
    var root = document.getElementById("wf-function-points");
    if (!root) return;
    functionPoints = functionPoints || {};
    var counts = functionPoints.counts || {};
    var rows = functionPoints.items || [];
    root.innerHTML =
      '<div class="workflow-v2-summary">' +
        '<span><small>场景计划</small><b>' + escapeHtml(provided(counts.declared)) + '</b></span>' +
        '<span><small>执行中</small><b>' + escapeHtml(provided(counts.executing)) + '</b></span>' +
        '<span><small>已验证</small><b>' + escapeHtml(provided(counts.verified)) + '</b></span>' +
        '<span><small>失败</small><b>' + escapeHtml(provided(counts.failed)) + '</b></span>' +
      '</div>' +
      '<div class="workflow-function-grid">' + (rows.length ? rows.map(function (row) {
        return '<div class="workflow-function-point ' + escapeHtml(row.status || "declared") + '">' +
          '<span>' + escapeHtml(row.function_point_id) + '</span><b>' + escapeHtml(row.name || row.function_point_id) + '</b>' +
          '<small>' + escapeHtml(statusLabel(row.status)) + (row.category ? ' · ' + escapeHtml(row.category) : '') + '</small></div>';
      }).join("") : '<div class="workflow-empty">当前场景未声明功能点</div>') + '</div>';
  }

  function renderExecutionGraph(graph) {
    var root = document.getElementById("wf-execution-graph");
    if (!root) return;
    graph = graph || {};
    var edges = graph.edges || [];
    root.innerHTML = '<div class="workflow-graph-note">依赖来源：' + escapeHtml(graph.dependency_source === "backend" ? "后端显式依赖" : "后端未上报") + '</div>' +
      '<div class="workflow-graph-edges">' + (edges.length ? edges.map(function (edge) {
        return '<span>' + escapeHtml(edge.source) + ' → ' + escapeHtml(edge.target) + '</span>';
      }).join("") : '<span>无显式依赖边</span>') + '</div>';
  }

  function renderProvenance(provenance) {
    var root = document.getElementById("wf-provenance");
    if (!root) return;
    provenance = provenance || {};
    var inputs = provenance.inputs || [];
    var activities = provenance.activities || [];
    root.innerHTML =
      '<div class="workflow-provenance-head">' +
        '<span><small>Run ID</small><b>' + escapeHtml(provided(provenance.run_id)) + '</b></span>' +
        '<span><small>Workflow ID</small><b>' + escapeHtml(provided(provenance.workflow_id)) + '</b></span>' +
        '<span><small>快照序号</small><b>' + escapeHtml(provided(provenance.snapshot_sequence)) + '</b></span>' +
      '</div>' +
      '<div class="workflow-provenance-list">' +
        (inputs.length ? inputs.map(function (row) {
          var checksum = row.checksum && row.checksum.value;
          return '<div><b>输入 · ' + escapeHtml(row.id || "未标识") + '</b><small>' + escapeHtml(row.kind || "data") + ' · SHA256 ' + escapeHtml(checksum ? String(checksum).slice(0, 8) : "未上报") + '</small></div>';
        }).join("") : '<div class="workflow-empty">无输入证据引用</div>') +
        (activities.length ? activities.map(function (row) {
          return '<div><b>输出 · ' + escapeHtml(row.activity_id || row.work_item || "未标识活动") + '</b><small>' +
            escapeHtml(provided(row.agent)) + ' · ' + escapeHtml(statusLabel(row.status)) +
            (row.algorithm_ids && row.algorithm_ids.length ? ' · ' + escapeHtml(row.algorithm_ids.join(", ")) : '') + '</small></div>';
        }).join("") : '') +
      '</div>';
  }

  function renderPerformanceMetrics(metrics) {
    var root = document.getElementById("wf-performance-metrics");
    if (!root) return;
    metrics = metrics || {};
    var rows = [
      ["工作流耗时", provided(metrics.workflow_duration_ms, function (value) { return Math.round(Number(value)) + " ms"; })],
      ["活动累计耗时", provided(metrics.activity_duration_total_ms, function (value) { return Math.round(Number(value)) + " ms"; })],
      ["调用尝试", provided(metrics.agent_call_attempts)],
      ["成功调用", provided(metrics.agent_call_completed)],
      ["失败调用", provided(metrics.agent_call_failed)],
      ["实际重试", provided(metrics.retry_count)],
      ["吞吐", provided(metrics.throughput)],
      ["恢复时间", provided(metrics.recovery_time_ms, function (value) { return Math.round(Number(value)) + " ms"; })],
    ];
    root.innerHTML = '<div class="workflow-metric-grid">' + rows.map(function (row) {
      return '<span><small>' + escapeHtml(row[0]) + '</small><b>' + escapeHtml(row[1]) + '</b></span>';
    }).join("") + '</div>';
  }

  function renderEvidenceViews(view) {
    view = view || {};
    renderAgentTopology(view.agents);
    renderAlgorithmCoverage(view.algorithms);
    renderFunctionPoints(view.function_points);
    renderExecutionGraph(view.execution_graph);
    renderProvenance(view.provenance);
    renderPerformanceMetrics(view.metrics);
  }

  function processActiveView(view) {
    if (!view || String(view.workflow_id || "") !== String(activeWorkflowId || "")) return;
    var analysis = view.result && view.result.analysis || {};
    var analysisSignature = JSON.stringify(analysis);
    if ((!view.run || view.run.current !== false) && Object.keys(analysis).length &&
        analysisSignature !== activeAnalysisSignature && options.onAnalysis) {
      activeAnalysisSignature = analysisSignature;
      options.onAnalysis(analysis);
    }
    if (!view.terminal) return;
    stopPolling();
    refreshRunHistory(true);
    var terminalKey = (view.workflow_id || "") + ":" + view.status;
    if (terminalKey !== notifiedTerminalKey) {
      notifiedTerminalKey = terminalKey;
      if (options.onTerminal) options.onTerminal(view);
    }
  }

  function renderView(view) {
    if (!view) return;
    var viewSignature = JSON.stringify(view);
    if (viewSignature === lastViewSignature) return;
    lastViewSignature = viewSignature;
    lastView = view;
    workflowId = view.workflow_id || workflowId;
    if (view.workflow_id) viewCache[String(view.workflow_id)] = view;
    workflowRunId = view.run && view.run.run_id ||
      view.submission && view.submission.run_id || workflowRunId;
    if (activeWorkflowId) sessionStorage.setItem("amos.workflow.current", activeWorkflowId);
    renderTaskHistory();
    setBadge(document.getElementById("wf-status-badge"), view.run && view.run.current === false ? "stale_run" : view.status);
    var stateEl = document.getElementById("wf-state");
    var idEl = document.getElementById("wf-id");
    var bar = document.getElementById("wf-progress-bar");
    var summary = document.getElementById("wf-summary");
    if (stateEl) stateEl.textContent = statusLabel(view.status);
    if (idEl) idEl.textContent = workflowId || "—";
    if (bar) bar.style.width = Math.max(0, Math.min(100, Number(view.progress_pct || 0))) + "%";
    var counts = (view.orchestration || {}).counts || {};
    if (summary) summary.textContent = view.backend && !view.backend.available
      ? "分析服务不可用：" + errorMessage(view.backend.error)
      : (view.run && view.run.current === false
          ? "该任务属于上一轮仿真，结果未写入当前态势"
          : "已完成 " + (counts.completed || 0) + "/" + (counts.total || 0) + " · " + statusLabel(view.status));
    renderSubmission(view.submission);
    renderActivities(view);
    renderResults(view.result);
    renderEvidenceViews(view);
    document.dispatchEvent(new CustomEvent("amos:workflow-view", {detail: view}));
    var resume = document.getElementById("btn-workflow-resume");
    if (resume) resume.hidden = !(view.recovery && view.recovery.can_resume);
    processActiveView(view);
  }

  async function loadView(id, generation) {
    if (!id) return null;
    var view = await API.getWorkflowView(id);
    if (generation != null && (generation !== viewGeneration || String(id) !== String(activeWorkflowId || ""))) return null;
    viewCache[String(id)] = view;
    renderTaskHistory();
    if (String(id) === String(workflowId || "")) renderView(view);
    else processActiveView(view);
    return view;
  }

  function startPolling(id) {
    stopPolling();
    activeWorkflowId = String(id);
    workflowId = String(id);
    selectedActivityId = null;
    lastViewSignature = null;
    activeAnalysisSignature = null;
    var generation = viewGeneration;
    loadView(id, generation).catch(function () {});
    pollTimer = setInterval(function () { loadView(id, generation).catch(function () {}); }, 2000);
  }

  function stopPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
    viewGeneration += 1;
  }

  function resetWorkflowDisplay() {
    stopPolling();
    workflowId = null;
    activeWorkflowId = null;
    workflowRunId = null;
    lastView = null;
    lastViewSignature = null;
    runManifest = null;
    historyRunId = null;
    historyLoadingRunId = null;
    viewCache = {};
    selectedActivityId = null;
    activityTab = "input";
    followCurrentActivity = true;
    activeAnalysisSignature = null;
    runHistoryGeneration += 1;
    notifiedTerminalKey = null;
    sessionStorage.removeItem("amos.workflow.current");
    setBadge(document.getElementById("wf-status-badge"), "");
    var state = document.getElementById("wf-state");
    var id = document.getElementById("wf-id");
    var summary = document.getElementById("wf-summary");
    var bar = document.getElementById("wf-progress-bar");
    var resume = document.getElementById("btn-workflow-resume");
    if (state) state.textContent = "—";
    if (id) id.textContent = "—";
    if (summary) summary.textContent = "当前仿真尚未进入后端分析检查点";
    if (bar) bar.style.width = "0%";
    if (resume) resume.hidden = true;
    var follow = document.getElementById("wf-follow-current");
    if (follow) follow.checked = true;
    renderSubmission(null);
    renderActivities({});
    renderResults({});
    renderEvidenceViews({});
    renderTaskHistory();
    document.dispatchEvent(new CustomEvent("amos:workflow-view", {detail: null}));
    selectTab("input", false);
  }

  async function submit() {
    if (submitting) return null;
    if (options.hasSimulationState && !options.hasSimulationState()) {
      alert("请先启动或重置仿真，生成可提交的当前时刻快照");
      return null;
    }
    if (options.getRunId) simulationRunId = options.getRunId() || simulationRunId;
    submitting = true;
    setBadge(document.getElementById("wf-status-badge"), "submitting");
    var buttons = [];
    buttons.forEach(function (button) { button.disabled = true; });
    try {
      var response = await API.submitA2AWorkflow({
        scenario_id: options.getScenarioId ? options.getScenarioId() : null,
        sim_context: true,
        workflow: "bpel",
        workflow_file: "integrated_system/workflows/integrated_demo_workflow.bpel",
      });
      var data = response.data || {};
      renderSubmission(data.amos_submission || {});
      if (!data.workflow_id) {
        setBadge(document.getElementById("wf-status-badge"), "error");
        var summary = document.getElementById("wf-summary");
        if (summary) summary.textContent = "提交失败：" + errorMessage(data.detail || data.code || "后端未返回 workflow_id");
        selectTab("input");
        return null;
      }
      var submittedRunId = data.amos_submission && data.amos_submission.run_id || null;
      if (submittedRunId && simulationRunId && String(submittedRunId) !== String(simulationRunId)) {
        setBadge(document.getElementById("wf-status-badge"), "stale_run");
        var staleSummary = document.getElementById("wf-summary");
        if (staleSummary) staleSummary.textContent = "任务已提交，但仿真已重置；该结果不会写入当前态势";
        return data;
      }
      workflowId = data.workflow_id;
      workflowRunId = submittedRunId;
      sessionStorage.setItem("amos.workflow.current", workflowId);
      selectTab("orchestration");
      startPolling(workflowId);
      return data;
    } catch (error) {
      setBadge(document.getElementById("wf-status-badge"), "error");
      var failureData = error && error.payload && error.payload.data || {};
      if (failureData.amos_submission) renderSubmission(failureData.amos_submission);
      var failureSummary = document.getElementById("wf-summary");
      if (failureSummary) failureSummary.textContent = "提交失败：" + errorMessage(error && error.message);
      selectTab("input");
      return null;
    } finally {
      submitting = false;
      buttons.forEach(function (button) { button.disabled = false; });
    }
  }

  async function resume() {
    if (!workflowId) return;
    try {
      await API.resumeWorkflow(workflowId, {});
      selectTab("orchestration");
      startPolling(workflowId);
    } catch (error) {
      setBadge(document.getElementById("wf-status-badge"), "error");
      var summary = document.getElementById("wf-summary");
      if (summary) summary.textContent = "恢复失败：" + errorMessage(error && error.message);
    }
  }

  async function checkHealth() {
    var badge = document.getElementById("backend-health-badge");
    var transportBadge = document.getElementById("backend-transport-badge");
    var bottom = document.getElementById("backend-status");
    try {
      var health = await API.getBackendHealth();
      var transport = health && health.transport || "gateway";
      var state = health && health.status === "ok" ? "connected" :
        (health && health.status === "degraded" ? "degraded" : "offline");
      setBadge(badge, state);
      if (transportBadge) {
        transportBadge.textContent = transport === "commander" ? "Commander 直连" : "Gateway";
        transportBadge.className = "badge " + (transport === "commander" ? "badge-yellow" : "badge-cyan");
      }
      if (bottom) {
        bottom.textContent = (transport === "commander" ? "直连 / " : "Gateway / ") + statusLabel(state);
        bottom.style.color = state === "connected" ? "#00ff41" : (state === "degraded" ? "#ffaa00" : "#ff4444");
      }
    } catch (error) {
      setBadge(badge, "offline");
      if (bottom) { bottom.textContent = "离线"; bottom.style.color = "#ff4444"; }
    }
  }

  function selectHistoryWorkflow(id) {
    if (!id || String(id) === String(workflowId || "")) return;
    workflowId = String(id);
    selectedActivityId = null;
    lastViewSignature = null;
    renderTaskHistory();
    if (viewCache[workflowId]) renderView(viewCache[workflowId]);
    loadView(workflowId, null).catch(function () {
      var summary = document.getElementById("wf-summary");
      if (summary) summary.textContent = "无法读取该后端工作流记录";
    });
  }

  function init(api, initOptions) {
    API = api;
    options = initOptions || {};
    document.querySelectorAll("[data-workflow-tab]").forEach(function (button) {
      button.addEventListener("click", function () { selectTab(this.dataset.workflowTab); });
    });
    var resumeButton = document.getElementById("btn-workflow-resume");
    if (resumeButton) resumeButton.addEventListener("click", resume);
    var taskHistory = document.getElementById("wf-task-history");
    if (taskHistory) taskHistory.addEventListener("click", function (event) {
      var button = event.target.closest("[data-workflow-history]");
      if (button) selectHistoryWorkflow(button.dataset.workflowHistory);
    });
    var activityList = document.getElementById("wf-activity-list");
    if (activityList) activityList.addEventListener("click", function (event) {
      var button = event.target.closest("[data-activity-id]");
      if (!button || !lastView) return;
      selectedActivityId = button.dataset.activityId;
      followCurrentActivity = false;
      var follow = document.getElementById("wf-follow-current");
      if (follow) follow.checked = false;
      renderActivities(lastView);
    });
    document.querySelectorAll("[data-activity-tab]").forEach(function (button) {
      button.addEventListener("click", function () {
        activityTab = this.dataset.activityTab;
        renderActivityDetail(lastView);
      });
    });
    var follow = document.getElementById("wf-follow-current");
    if (follow) follow.addEventListener("change", function () {
      followCurrentActivity = this.checked;
      if (lastView) renderActivities(lastView);
    });
    checkHealth();
    if (healthTimer) clearInterval(healthTimer);
    healthTimer = setInterval(checkHealth, 10000);
    renderSubmission(null);
    renderActivities({});
    renderResults({});
    renderEvidenceViews({});
    renderTaskHistory();
    selectTab("input", false);
    var restored = sessionStorage.getItem("amos.workflow.current");
    if (restored) startPolling(restored);
  }

  function syncRun(runId) {
    if (!runId) return;
    var previousRunId = simulationRunId;
    simulationRunId = String(runId);
    if (previousRunId && String(previousRunId) !== simulationRunId) {
      resetWorkflowDisplay();
      refreshRunHistory(true);
      return;
    }
    if (historyRunId !== simulationRunId) refreshRunHistory(false);
    var taskRunId = workflowRunId ||
      lastView && lastView.run && lastView.run.run_id ||
      lastView && lastView.submission && lastView.submission.run_id;
    if (!workflowId || !taskRunId || String(taskRunId) === simulationRunId) return;
    resetWorkflowDisplay();
  }

  function track(id, runId) {
    if (!id) return;
    workflowId = String(id);
    workflowRunId = runId ? String(runId) : workflowRunId;
    if (runId) simulationRunId = String(runId);
    sessionStorage.setItem("amos.workflow.current", workflowId);
    selectTab("orchestration");
    startPolling(workflowId);
    refreshRunHistory(true);
  }

  return {
    init: init,
    submit: submit,
    loadView: loadView,
    checkHealth: checkHealth,
    selectTab: selectTab,
    syncRun: syncRun,
    track: track,
    reset: resetWorkflowDisplay,
  };
})();
