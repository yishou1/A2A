/* platform-panels.js — current tracks, alerts, asset health, and network status */

window.PlatformPanels = (function () {

  var activeScenario = null;
  var contactAliases = {};
  var nextContactAlias = 1;
  var contactRunId = null;
  var runtimeAlgorithmCatalog = null;

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function setHtmlIfChanged(element, html) {
    if (!element || element.innerHTML === html) return false;
    element.innerHTML = html;
    return true;
  }

  function contactClassificationLabel(track) {
    var assessment = track && track.agent_assessment || {};
    if (!assessment.source) return "";
    var classification = String(track.classification || "UNKNOWN").toUpperCase();
    var labels = {
      FAST_ATTACK_CRAFT: "高速攻击艇",
      MISSILE_BOAT: "导弹艇",
      SURFACE_COMBATANT: "水面作战舰艇",
      FISHING_VESSEL: "民用渔船",
      FISHING: "民用渔船",
      CIVILIAN: "民用船只",
      MERCHANT: "商船",
      MERCHANT_VESSEL: "商船",
    };
    if (labels[classification]) return labels[classification];
    return classification === "UNKNOWN" ? "" : classification;
  }

  function contactLabel(track) {
    var id = String(track && (track.id || track.track_id) || "");
    if (!id) return "海面接触";
    var displayLabel = String(track && track.display_label || "");
    if (displayLabel) {
      contactAliases[id] = displayLabel;
    } else if (!contactAliases[id]) {
      contactAliases[id] = "海面接触 " + String(nextContactAlias++).padStart(2, "0");
    }
    var classification = contactClassificationLabel(track);
    return contactAliases[id] + (classification ? " · " + classification : "");
  }

  function syncContactRun(clock) {
    if (!clock || !clock.run_id || String(clock.run_id) === String(contactRunId || "")) return;
    contactRunId = String(clock.run_id);
    contactAliases = {};
    nextContactAlias = 1;
  }

  // ── Track List ─────────────────────────────────────────────

  function updateTracks(tracks, weapons) {
    var container = document.getElementById("track-list");
    var badge = document.getElementById("track-count-badge");
    if (!container) return;

    if (!tracks || !tracks.length) {
      setHtmlIfChanged(container, '<div class="track-empty">暂无融合航迹</div>');
      if (badge) badge.textContent = "0";
      return;
    }

    if (badge) badge.textContent = tracks.length;

    var html = tracks.map(function (t) {
      var assessment = t.agent_assessment || {};
      var assessed = Boolean(assessment.source);
      var cls = assessed ? (t.classification || "UNKNOWN") : "未分类";
      var assessmentLabel = assessed ? (assessment.label || t.threat_level || "已评估") : "待分析";
      var assessmentColor = assessment.status === "confirmed" ? "#ff6644" : (assessment.status === "watch" ? "#ffaa00" : (assessed ? "#41cfff" : "#7d8b8e"));
      var conf = Number(t.confidence || 0);
      var sc = t.source_count || 0;
      var classification = String(t.classification || "UNKNOWN").toUpperCase();
      var protectedTarget = /FISHING|CIVILIAN|MERCHANT/.test(classification);
      var alreadyEngaged = (weapons || []).some(function (weapon) {
        return String(weapon.target_track_id || "") === String(t.id || t.track_id || "") &&
          ["in_flight", "hit"].indexOf(String(weapon.status || "")) >= 0;
      });
      var action = protectedTarget
        ? '<span class="track-no-strike">民用禁射</span>'
        : alreadyEngaged
          ? '<span class="track-engaged">攻击已下达</span>'
          : "";

      return '<div class="track-row">' +
        '<div class="track-row-head"><span class="track-id" title="' + escapeHtml(t.id || t.track_id || "") + '">' +
        escapeHtml(contactLabel(t)) + '</span>' +
        '<span class="track-level" style="color:' + assessmentColor + '">' + escapeHtml(assessmentLabel) + '</span></div>' +
        '<div class="track-system-id">' + escapeHtml(t.id || t.track_id || "") + '</div>' +
        '<div class="track-row-meta"><span class="track-class">类别 ' + escapeHtml(cls) + '</span>' +
        '<span class="track-conf">置信度 ' + conf.toFixed(2) + '</span>' +
        '<span class="track-src">传感器源 ' + sc + '</span></div>' +
        (assessed ? '<div class="track-assessment-source">评估来源 ' + escapeHtml(assessment.source) + '</div>' : '') +
        action +
        '</div>';
    }).join("");
    setHtmlIfChanged(container, html);
  }

  // ── Alert List ─────────────────────────────────────────────

  function updateAlerts(alerts) {
    var container = document.getElementById("alert-list");
    var badge = document.getElementById("alert-count-badge");
    if (!container) return;

    if (!alerts || !alerts.length) {
      setHtmlIfChanged(container, '<div class="track-empty">暂无事件或告警</div>');
      if (badge) {
        badge.textContent = "0";
        badge.className = "badge badge-cyan";
      }
      return;
    }

    if (badge) {
      badge.textContent = alerts.length;
      badge.className = "badge " + (alerts.some(function (item) { return item.level === "CRITICAL"; })
        ? "badge-red" : alerts.some(function (item) { return item.level === "WARNING"; })
          ? "badge-yellow" : "badge-cyan");
    }

    var levelLabels = {
      "CRITICAL": "严重",
      "WARNING": "警告",
      "INFO": "信息"
    };

    // Show last 8, newest first
    var recent = alerts.slice(-8).reverse();

    setHtmlIfChanged(container, recent.map(function (a) {
      var label = levelLabels[a.level] || "事件";
      var cls = a.level === "CRITICAL" ? "alert-critical" : a.level === "WARNING" ? "alert-warning" : "alert-info";
      return '<div class="alert-row ' + cls + '">' +
        '<span class="alert-icon">' + escapeHtml(label) + '</span>' +
        '<span class="alert-msg">' + escapeHtml(a.msg || "") + '</span>' +
        '</div>';
    }).join(""));
  }

  // ── Asset Health Overview ───────────────────────────────────

  function updateAssetHealth(assets) {
    var container = document.getElementById("asset-health-list");
    var badge = document.getElementById("asset-count-badge");
    if (!container) return;

    if (!assets || !assets.length) {
      setHtmlIfChanged(container, '<div class="track-empty">暂无平台状态</div>');
      if (badge) badge.textContent = "0";
      return;
    }

    if (badge) badge.textContent = assets.length;

    var domainLabels = {air: "AIR", maritime: "SEA", ground: "SITE"};
    var statusLabels = {
      active: "正常", operational: "正常", "comm-lost": "通信中断",
      autonomous: "自主运行", staged: "待命", holding: "保持",
      degraded: "降级", unavailable: "不可用", inactive: "离线"
    };

    setHtmlIfChanged(container, assets.map(function (a) {
      var usesBattery = a.battery_pct != null;
      var energy = usesBattery ? Number(a.battery_pct) :
        (a.fuel_pct == null ? null : Number(a.fuel_pct));
      var comms = a.comms_strength == null ? 100 : Number(a.comms_strength);
      var status = a.status || "operational";
      var domainLabel = domainLabels[a.domain] || "N/A";

      var energyColor = energy == null ? "#7d8b8e" : energy > 70 ? "#00ff41" : energy > 30 ? "#ffaa00" : "#ff4444";
      var commsColor = comms > 70 ? "#00ff41" : comms > 30 ? "#ffaa00" : "#ff4444";
      var statusColor = status === "active" || status === "operational" || status === "holding"
        ? "#00ff41" : status === "comm-lost" || status === "unavailable"
          ? "#ff4444" : status === "degraded" || status === "staged"
            ? "#ffaa00" : status === "autonomous" ? "#00ccff" : "#888";
      var energyLabel = usesBattery ? "电量" : "燃油";
      var energyText = energy == null ? "未提供" : energy.toFixed(0) + "%";

      return '<div class="asset-health-row">' +
        '<span class="asset-icon">' + escapeHtml(domainLabel) + '</span>' +
        '<span class="asset-id" title="' + escapeHtml(a.role || a.id) + '">' + escapeHtml((a.role || a.id || "?").substring(0, 12)) + '</span>' +
        '<span class="asset-status" style="color:' + statusColor + '">' + escapeHtml(statusLabels[status] || status) + '</span>' +
        '<span class="asset-batt" style="color:' + energyColor + '" title="剩余能源">' + energyLabel + ' ' + energyText + '</span>' +
        '<span class="asset-comms" style="color:' + commsColor + '" title="通信链路质量">链路 ' + comms.toFixed(0) + '%</span>' +
        '</div>';
    }).join(""));
  }

  // ── Network Status ─────────────────────────────────────────

  function updateNetwork(net) {
    if (!net) return;
    var nodesEl = document.getElementById("net-nodes");
    var linksEl = document.getElementById("net-links");
    var resilienceEl = document.getElementById("net-resilience");
    var degradedEl = document.getElementById("net-degraded");

    if (nodesEl) nodesEl.textContent = net.nodes || 0;
    if (linksEl) linksEl.textContent = net.links || 0;
    if (resilienceEl) {
      var r = net.resilience || 0;
      resilienceEl.textContent = r.toFixed(0) + "%";
      resilienceEl.className = "net-value " + (r > 70 ? "net-good" : r > 40 ? "net-warn" : "net-bad");
    }
    if (degradedEl) degradedEl.textContent = net.degraded_links || 0;
  }

  // ── Status Bar ─────────────────────────────────────────────

  function updateStatusBar(state) {
    var clock = state.clock || {};
    var elapsed = clock.elapsed_sec || 0;
    var hours = Math.floor(elapsed / 3600);
    var mins = Math.floor((elapsed % 3600) / 60);
    var secs = Math.floor(elapsed % 60);
    var clockStr = String(hours).padStart(2, "0") + ":" +
      String(mins).padStart(2, "0") + ":" + String(secs).padStart(2, "0");

    var clockEl = document.getElementById("status-clock");
    if (clockEl) clockEl.textContent = clockStr;

    var assetsEl = document.getElementById("status-assets");
    if (assetsEl) assetsEl.textContent = (state.assets || []).length;

    var tracksEl = document.getElementById("status-tracks");
    if (tracksEl) tracksEl.textContent = (state.fused_tracks || []).length;

    // Live dot
    var dot = document.getElementById("live-dot");
    if (dot) {
      dot.style.backgroundColor = clock.running ? "#00ff41" : "#ff4444";
      dot.style.boxShadow = clock.running ? "0 0 6px #00ff41" : "none";
    }
  }

  // ── Task workspace (workflow-view v2 compatible) ───────────

  function asList(value) {
    if (Array.isArray(value)) return value;
    if (!value || typeof value !== "object") return [];
    return Object.keys(value).map(function (key) {
      var item = value[key];
      return typeof item === "object" && item !== null
        ? Object.assign({id: key}, item) : {id: key, name: item};
    });
  }

  function compactValue(value) {
    if (value == null || value === "") return "未声明";
    if (Array.isArray(value)) {
      var items = value.map(function (item) {
        if (!item || typeof item !== "object") return String(item);
        return String(item.key || item.id || item.name || "指标") +
          (item.value == null ? "" : "=" + String(item.value));
      }).filter(Boolean);
      return items.length ? escapeHtml(items.join("；")) : "未声明";
    }
    if (typeof value === "object") {
      var keys = Object.keys(value).slice(0, 5);
      return keys.length ? escapeHtml(keys.map(function (key) {
        return key + "=" + String(value[key]);
      }).join("；")) : "未声明";
    }
    return escapeHtml(value);
  }

  function renderFunctionalAgents(scenario) {
    var root = document.getElementById("scenario-functional-agents");
    var badge = document.getElementById("functional-agent-count");
    if (!root) return;
    var rows = asList(scenario && scenario.functional_agents);
    if (badge) badge.textContent = rows.length + "/6";
    setHtmlIfChanged(root, rows.length ? rows.map(function (item) {
      return '<article class="agent-node planned"><header><b>' +
        escapeHtml((item.agent_id || "—") + " · " + (item.name || "未命名 Agent")) +
        '</b><span>计划职责</span></header><p>' + compactValue(item.responsibilities) +
        '</p><dl><div><dt>后端角色</dt><dd>' + compactValue(item.backend_roles) +
        '</dd></div></dl></article>';
    }).join("") : '<div class="empty-state">当前场景未声明功能 Agent</div>');
  }

  function renderBackendCapabilities() {
    var root = document.getElementById("backend-capability-map");
    if (!root) return;
    var catalog = runtimeAlgorithmCatalog || {};
    var allRows = Array.isArray(catalog.algorithms) ? catalog.algorithms : [];
    var readyRows = allRows.filter(function (item) { return item.runtime_status === "ready"; });
    var unavailableRows = allRows.filter(function (item) { return item.runtime_status !== "ready"; });
    var families = {};
    allRows.forEach(function (item) { if (item.task_family) families[item.task_family] = true; });
    var activeCount = catalog.active_count;
    if (activeCount == null) activeCount = allRows.length;
    var activeNode = document.getElementById("backend-active-count");
    if (activeNode) activeNode.textContent = activeCount || 0;
    document.getElementById("backend-runnable-count").textContent = catalog.runnable_count || 0;
    document.getElementById("backend-unavailable-count").textContent = catalog.unavailable_count || 0;
    document.getElementById("backend-family-count").textContent = Object.keys(families).length;
    var status = document.getElementById("algorithm-runtime-status");
    if (status) {
      status.textContent = catalog.status === "ready" ? "运行时已验证" :
        (catalog.status === "degraded" ? "部分服务不可用" : (catalog.status === "offline" ? "算法库离线" : "检查中"));
      status.className = "status-chip " + (catalog.status === "ready" ? "success" :
        (catalog.status === "degraded" ? "warning" : (catalog.status === "offline" ? "danger" : "neutral")));
    }
    var meta = document.getElementById("algorithm-runtime-meta");
    if (meta) {
      var checkedAt = catalog.checked_at ? new Date(catalog.checked_at).toLocaleString() : "未上报";
      meta.innerHTML = '<span>来源 <b>' + escapeHtml(catalog.source || "未上报") + '</b></span>' +
        '<span>最近检查 <b>' + escapeHtml(checkedAt) + '</b></span>';
    }
    function renderAlgorithmCard(item) {
      var profile = item.model_profile || {};
      var params = profile.parameter_count_text || (profile.parameter_count == null ? "未上报" : String(profile.parameter_count));
      var ready = item.runtime_status === "ready";
      var stateText = ready ? "运行就绪" : "不可用";
      var endpoint = item.predict_endpoint || profile.predict_endpoint || "未上报";
      return '<article class="backend-function-card ' + (ready ? "runtime-ready" : "runtime-unavailable") + '">' +
        '<header><div><b>' + escapeHtml(item.display_name || item.algorithm_id) + '</b><code>' +
        escapeHtml(item.algorithm_id) + '</code></div><span>' + escapeHtml(stateText) + '</span></header>' +
        '<p>' + escapeHtml((item.capabilities || []).join(" · ") || "未声明能力标签") + '</p>' +
        '<div class="backend-runtime-meta"><span>任务族 <b>' + escapeHtml(item.task_family || "未上报") + '</b></span>' +
        '<span>模型 <b>' + escapeHtml(profile.model_id || "未上报") + '</b></span>' +
        '<span>版本 <b>' + escapeHtml(item.version || "未上报") + '</b></span>' +
        '<span>后端 <b>' + escapeHtml(item.backend_type || "未上报") + '</b></span>' +
        '<span>模型规模 <b>' + escapeHtml(params) + '</b></span>' +
        '<span>预测端点 <b>' + escapeHtml(endpoint) + '</b></span></div></article>';
    }
    function renderGroup(title, rows) {
      if (!rows.length) return "";
      var grouped = rows.reduce(function (result, item) {
        var family = item.task_family || "未分组";
        if (!result[family]) result[family] = [];
        result[family].push(item);
        return result;
      }, {});
      return '<section class="backend-family-section"><header><b>' + escapeHtml(title) +
        '</b><span>' + escapeHtml(rows.length) + '</span></header>' +
        Object.keys(grouped).sort().map(function (family) {
          var items = grouped[family];
          return '<div class="backend-family-group"><div class="backend-family-title"><b>' +
            escapeHtml(family) + '</b><span>' + escapeHtml(items.length) + '</span></div>' +
            items.map(renderAlgorithmCard).join("") + '</div>';
        }).join("") + '</section>';
    }
    var html = allRows.length ? renderGroup("可调度算法", readyRows) + renderGroup("运行时异常", unavailableRows) :
      '<div class="empty-state">' + escapeHtml(catalog.error || "后端当前没有返回算法目录") + '</div>';
    setHtmlIfChanged(root, html);
  }

  function updateRuntimeAlgorithms(catalog) {
    runtimeAlgorithmCatalog = catalog || {status: "offline", algorithms: []};
    renderBackendCapabilities();
  }

  function updateWorkspace(state, scenario) {
    activeScenario = scenario || activeScenario;
    var clock = state && state.clock || {};
    syncContactRun(clock);
    var runId = document.getElementById("workspace-run-id");
    if (runId) runId.textContent = clock.run_id ? "RUN " + clock.run_id : "RUN —";
    var phases = state && state.mission_phases || {};
    [["F2T2EA", phases.f2t2ea], ["OODA", phases.ooda]].forEach(function (entry) {
      var rows = entry[1] && entry[1].items || [];
      var byPhase = rows.reduce(function (result, row) {
        result[String(row.phase || "").toUpperCase()] = String(row.status || "pending");
        return result;
      }, {});
      document.querySelectorAll('.phase-track[data-phase-model="' + entry[0] + '"] span').forEach(function (element) {
        var status = byPhase[String(element.textContent || "").toUpperCase()] || "pending";
        element.className = status;
      });
    });
    renderBackendCapabilities();
    renderFunctionalAgents(activeScenario);
  }

  // ── Full Update ────────────────────────────────────────────

  function updateAll(state) {
    if (!state) return;
    syncContactRun(state.clock || {});
    updateTracks(state.fused_tracks, state.weapons);
    updateAlerts(state.alerts);
    updateAssetHealth(state.assets);
    updateNetwork(state.network);
    updateStatusBar(state);
  }

  return {
    updateTracks: updateTracks,
    updateAlerts: updateAlerts,
    updateAssetHealth: updateAssetHealth,
    updateNetwork: updateNetwork,
    updateStatusBar: updateStatusBar,
    updateAll: updateAll,
    updateWorkspace: updateWorkspace,
    contactLabel: contactLabel,
    updateRuntimeAlgorithms: updateRuntimeAlgorithms,
  };
})();
