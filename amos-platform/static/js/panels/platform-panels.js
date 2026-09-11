/* platform-panels.js — current tracks, alerts, asset health, and network status */

window.PlatformPanels = (function () {

  var activeScenario = null;
  var contactAliases = {};
  var nextContactAlias = 1;
  var contactRunId = null;
  var runtimeAlgorithmCatalog = null;
  var RUNTIME_ALGORITHM_I18N = {
    battlefield_rtdetr_detector: ["战场 RT-DETR 目标检测器", "来自 TIA 战术情报智能体流水线的战场实时目标检测实现。"],
    closed_loop_decision_advisor: ["闭环决策顾问", "面向 A2A 算法库封装的闭环决策建议实现。"],
    clustering_engine: ["确定性聚类引擎", "在 CPU 上提供确定性的 K-Means 与 DBSCAN 聚类实现。"],
    compliance_authorization_core: ["合规授权核心", "集成权威规则匹配、SynapseRAG 证据检索和 ONNX 风险校准的合规审查实现。"],
    conditional_tabular_gan: ["条件表格生成对抗网络", "经训练的 PyTorch 条件 GAN，可按三类指定条件生成有界的五维合成参考观测。"],
    decision_planning_core: ["决策规划核心", "集成 ONNX 评分、结构化规则和可选 SynapseRAG 证据的决策规划实现。"],
    edl_evidential_verifier: ["EDL 证据验证器", "经训练的狄利克雷证据分类器，输出校准后的验证概率、认知与偶然不确定性及可复核证据链。"],
    execution_control_planner: ["执行控制规划器", "面向 A2A 算法库封装的任务执行控制与动作规划实现。"],
    execution_rule_matcher: ["执行规则匹配器", "基于持久化且经过哈希校验的规则制品，执行确定性的 Apriori 风格关联规则匹配。"],
    federated_fedavg_aggregator: ["联邦 FedAvg 聚合器", "按本地样本量加权聚合多个客户端模型更新，并校验各模型张量结构一致性。"],
    graph_relation_reasoner: ["图关系推理器", "面向航迹威胁智能体的 M20 实现，使用训练后的图神经网络推断编队关系与连通群组。"],
    imagebind_multimodal_encoder: ["ImageBind 多模态编码器", "来自 TIA 战术情报智能体流水线的 ImageBind 多模态特征编码实现。"],
    intent_gaussian_naive_bayes: ["意图高斯朴素贝叶斯", "使用冻结的高斯朴素贝叶斯模型，根据结构化观测估计良性、监视和敌对意图的后验概率。"],
    knowledge_semantic_comm: ["知识语义通信", "来自 TIA 战术情报智能体流水线的知识增强语义通信实现。"],
    marl_dynamic_router: ["MARL 动态路由器", "来自 TIA 战术情报智能体流水线的多智能体强化学习动态路由实现。"],
    marl_ppo_task_scheduler: ["MARL-PPO 任务调度器", "经训练的参数共享 MARL-PPO Actor-Critic 调度器，支持有效动作掩码和传感器、打击及再攻击任务分配。"],
    mission_completion_scorer: ["任务完成度评分器", "面向 A2A 算法库封装的任务完成状态与闭环效果评分实现。"],
    mission_feature_adapter: ["任务特征适配器", "面向 A2A 算法库封装的任务输入规范化与特征适配实现。"],
    motr_neural_kalman_tracker: ["MOTR 神经卡尔曼跟踪器", "来自 TIA 战术情报智能体流水线的多目标神经卡尔曼跟踪实现。"],
    multimodal_feature_fuser: ["多模态特征融合器", "面向航迹威胁智能体的 M03 实现，将检测、航迹、场景和被保护资源元数据融合为结构化特征。"],
    multimodal_mamba_fusion: ["多模态 Mamba 融合模型", "经训练的小型 Mamba 风格选择性状态空间模块，用于有序多模态嵌入序列的去噪与融合。"],
    onnx_text_classifier: ["ONNX 文本分类契约测试实现", "使用确定性常量 Logit 制品验证文本分词、ONNX Runtime 与分类后处理的集成契约。"],
    siamese_mask2former_damage: ["孪生 Mask2Former 毁伤评估器", "来自 TIA 战术情报智能体流水线的孪生 Mask2Former 毁伤变化评估实现。"],
    supcon_meta_classifier: ["SupCon 元分类器", "经训练的小型神经分类器，组合双层投影 MLP、监督式对比损失和可学习类别原型。"],
    synapse_rag_retriever: ["Synapse RAG 检索器", "来自 TIA 战术情报智能体流水线的知识证据检索增强实现。"],
    target_type_classifier: ["目标类型分类器", "面向航迹威胁智能体的 M04 实现，根据结构化检测与航迹字段分类或补全目标类型。"],
    threat_priority_random_forest: ["威胁优先级随机森林", "使用可复现参考数据集训练并冻结的随机森林，将结构化目标划分为低、中、高优先级。"],
    track_state_updater: ["航迹状态更新器", "面向航迹威胁智能体的 M05 实现，使用最近邻关联与轻量滤波更新结构化检测航迹。"],
    trajectory_linear_predictor: ["航迹线性预测器", "在 CPU 上按请求执行普通最小二乘回归，估计二维速度并外推未来瞄准点。"],
    trajectory_predictor: ["航迹预测器", "面向航迹威胁智能体的 M06 实现，优先使用冻结的 ST-GNN 模型包，并以物理模型作为后备。"],
    xbd_damage_assessor: ["xBD 毁伤评估器", "使用手工 ROI 特征、ResNet18 嵌入及持久化逻辑回归流水线的冻结 xBD 毁伤评估实现。"],
  };
  var TASK_FAMILY_I18N = {
    classification: "分类", clustering: "聚类", compliance: "合规审查", decision: "决策",
    detection: "目标检测", embedding: "特征编码", feature_engineering: "特征工程",
    federated_learning: "联邦学习", forecasting: "预测", fusion: "信息融合",
    generation: "数据生成", graph_reasoning: "图关系推理", planning: "任务规划",
    retrieval: "知识检索", scoring: "评分", segmentation: "图像分割",
    text_classification: "文本分类", tracking: "目标跟踪",
  };

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

  function localizedAlgorithm(item) {
    var englishName = item.display_name || item.algorithm_id || "未命名算法";
    var translated = RUNTIME_ALGORITHM_I18N[item.algorithm_id] || [];
    return {
      name: translated[0] ? translated[0] + "（" + englishName + "）" : englishName,
      summary: translated[1] || item.summary || (item.agent_card || {}).summary || "暂未提供算法简介。",
    };
  }

  function taskFamilyLabel(value) {
    var key = String(value || "未分组");
    return TASK_FAMILY_I18N[key] ? TASK_FAMILY_I18N[key] + "（" + key + "）" : key;
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
      COASTAL_MISSILE_SITE: "沿海导弹阵地",
      MISSILE_SITE: "导弹阵地",
      MISSILE_BATTERY: "导弹阵地",
      AIRFIELD_RUNWAY: "军用机场跑道",
      AIRFIELD: "军用机场",
      MOBILE_COASTAL_AIR_DEFENSE: "机动岸防雷达/防空单元",
      COASTAL_AIR_DEFENSE: "岸防防空单元",
      CIVILIAN_PORT: "民用渔港",
    };
    if (labels[classification]) return labels[classification];
    return classification === "UNKNOWN" ? "" : classification;
  }

  function contactLabel(track) {
    var id = String(track && (track.id || track.track_id) || "");
    if (!id) return "待识别接触";
    var displayLabel = String(track && track.display_label || "");
    if (displayLabel) {
      contactAliases[id] = displayLabel;
    } else if (!contactAliases[id]) {
      var domain = String(track && (track.domain_hint || track.domain) || "").toLowerCase();
      var prefix = domain === "ground" ? "地面接触" : (domain === "air" ? "空中接触" : "海面接触");
      contactAliases[id] = prefix + " " + String(nextContactAlias++).padStart(2, "0");
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

    var domainLabels = {air: "空中", maritime: "海上", ground: "地面", space: "太空"};
    var statusLabels = {
      active: "正常", operational: "正常", "comm-lost": "通信中断",
      autonomous: "自主运行", staged: "待命", holding: "保持",
      degraded: "降级", unavailable: "不可用", inactive: "离线"
    };

    setHtmlIfChanged(container, assets.map(function (a) {
      var usesBattery = a.battery_pct != null;
      var energy = usesBattery ? Number(a.battery_pct) :
        (a.fuel_pct == null ? null : Number(a.fuel_pct));
      var comms = a.comms_strength == null ? null : Number(a.comms_strength);
      var status = a.status || "operational";
      var domainLabel = domainLabels[a.domain] || "未提供";

      var energyColor = energy == null ? "#7d8b8e" : energy > 70 ? "#00ff41" : energy > 30 ? "#ffaa00" : "#ff4444";
      var commsColor = comms == null ? "#7d8b8e" : comms > 70 ? "#00ff41" : comms > 30 ? "#ffaa00" : "#ff4444";
      var statusColor = status === "active" || status === "operational" || status === "holding"
        ? "#00ff41" : status === "comm-lost" || status === "unavailable"
          ? "#ff4444" : status === "degraded" || status === "staged"
            ? "#ffaa00" : status === "autonomous" ? "#00ccff" : "#888";
      var energyLabel = usesBattery ? "电量" : a.fuel_pct != null ? "燃油" : "能源";
      var energyText = energy == null ? "未提供" : energy.toFixed(0) + "%";

      return '<div class="asset-health-row">' +
        '<span class="asset-icon">' + escapeHtml(domainLabel) + '</span>' +
        '<span class="asset-id" title="' + escapeHtml(a.role || a.id) + '">' + escapeHtml(a.role || a.id || "未命名平台") + '</span>' +
        '<span class="asset-status" style="color:' + statusColor + '">' + escapeHtml(statusLabels[status] || status) + '</span>' +
        '<span class="asset-batt" style="color:' + energyColor + '" title="剩余能源">' + energyLabel + ' ' + energyText + '</span>' +
        '<span class="asset-comms" style="color:' + commsColor + '" title="通信链路质量">链路 ' + (comms == null ? "未提供" : comms.toFixed(0) + '%') + '</span>' +
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
    if (value == null || value === "") return "未配置";
    if (Array.isArray(value)) {
      var items = value.map(function (item) {
        if (!item || typeof item !== "object") return String(item);
        return String(item.key || item.id || item.name || "指标") +
          (item.value == null ? "" : "=" + String(item.value));
      }).filter(Boolean);
      return items.length ? escapeHtml(items.join("；")) : "未配置";
    }
    if (typeof value === "object") {
      var keys = Object.keys(value).slice(0, 5);
      return keys.length ? escapeHtml(keys.map(function (key) {
        return key + "=" + String(value[key]);
      }).join("；")) : "未配置";
    }
    return escapeHtml(value);
  }

  function valueList(value) {
    if (Array.isArray(value)) return value;
    if (value == null || value === "") return [];
    return String(value).split(/[；, ]+/).filter(Boolean);
  }

  function renderFunctionalAgents(scenario) {
    var root = document.getElementById("scenario-functional-agents");
    var badge = document.getElementById("functional-agent-count");
    if (!root) return;
    var rows = asList(scenario && scenario.functional_agents);
    if (badge) badge.textContent = rows.length + "/6";
    var functionNames = functionNameMap(runtimeAlgorithmCatalog);
    function chips(values, className) {
      return values.length ? values.map(function (value) { return '<span class="agent-relation-chip ' + className + '">' + escapeHtml(value) + '</span>'; }).join("") : '<span class="agent-relation-empty">未绑定</span>';
    }
    var html = rows.length ? rows.map(function (item) {
      var skillRows = (item.skills || []).map(function (skill) {
        return '<section class="agent-skill-relation"><header><b>' + escapeHtml(skill.name || skill.skill_id) + '</b><small>' + escapeHtml(skill.skill_id || "") + '</small></header>' +
          '<div><label>主算法</label><p>' + chips((skill.primary || []).map(function (id) { return algorithmLabel(id, runtimeAlgorithmCatalog); }), "primary") + '</p></div>' +
          ((skill.supporting || []).length ? '<div><label>辅助算法</label><p>' + chips(skill.supporting.map(function (id) { return algorithmLabel(id, runtimeAlgorithmCatalog); }), "supporting") + '</p></div>' : '') +
          '<div><label>功能点</label><p>' + chips((skill.function_points || []).map(function (id) { return functionLabel(id, functionNames); }), "function") + '</p></div></section>';
      }).join("");
      return '<article class="agent-node planned"><header><b>' +
        escapeHtml((item.agent_id || "—") + " · " + (item.name || "未命名 Agent")) +
        '</b><span>职责</span></header><p>' + compactValue(item.responsibilities) +
        '</p><details class="presentation-details"><summary>查看角色与算法</summary><dl><div><dt>后端角色</dt><dd>' + compactValue(item.backend_roles) +
        '</dd></div></dl><div class="agent-skill-relations">' + (skillRows || '<div class="agent-relation-empty">未配置技能关系</div>') + '</div></details></article>';
    }).join("") : '<div class="empty-state">当前场景未配置功能 Agent</div>';
    // Opening <details> changes innerHTML; compare generated markup so state
    // refreshes do not collapse a section the reader is inspecting.
    if (root._agentCatalogHtml !== html) {
      root._agentCatalogHtml = html;
      setHtmlIfChanged(root, html);
    }
  }

  function functionNameMap(catalog) {
    var map = {};
    ((catalog && catalog.operational_functions) || []).forEach(function (item) {
      map[item.function_id] = item.name || item.function_name || item.function_id;
    });
    return map;
  }

  function functionLabel(id, names) {
    return id + ' · ' + ((names && names[id]) || id);
  }

  function algorithmLabel(id, catalog) {
    var runtimeMatch = ((catalog && catalog.algorithms) || []).filter(function (item) {
      return item.algorithm_id === id;
    })[0];
    if (runtimeMatch && (runtimeMatch.display_name || runtimeMatch.algorithm_id)) {
      return (runtimeMatch.display_name || runtimeMatch.algorithm_id) + '（' + id + '）';
    }
    var match = ((catalog && catalog.algorithm_classes) || []).filter(function (item) {
      return (item.primary_algorithm_ids || []).indexOf(id) >= 0 || (item.auxiliary_algorithm_ids || []).indexOf(id) >= 0;
    })[0];
    if (match) return match.requirement_id + ' · ' + match.name + '（' + id + '）';
    var knownNames = {
      mission_feature_adapter: "Mission Feature Adapter",
      execution_control_planner: "Execution Control Planner",
      mission_completion_scorer: "Mission Completion Scorer",
    };
    return knownNames[id] ? knownNames[id] + '（' + id + '）' : id;
  }

  function stageLabel(stage) {
    return ({find:"发现（Find）", fix:"定位（Fix）", track:"跟踪（Track）", target:"目标选择（Target）", engage:"交战（Engage）", assess:"评估（Assess）"})[String(stage || "").toLowerCase()] || String(stage || "未分组");
  }

  function renderAgentDeploymentTopology(scenario) {
    var root = document.getElementById("agent-deployment-topology");
    var badge = document.getElementById("agent-deployment-count");
    if (!root) return;
    var devices = asList(scenario && scenario.physical_devices);
    var nodes = asList(scenario && scenario.compute_nodes);
    var deployments = asList(scenario && scenario.agent_deployments);
    var agents = asList(scenario && scenario.functional_agents);
    var counts = [["平台", asList(scenario && scenario.assets).length], ["设备（含载荷）", devices.length],
      ["算力节点", nodes.length], ["部署关系", deployments.length]];
    setHtmlIfChanged(document.getElementById("presentation-resource-counts"), counts.map(function (row) {
      return '<span><small>' + row[0] + '</small><b>' + row[1] + '</b></span>';
    }).join(""));
    if (badge) badge.textContent = deployments.length + " 条部署关系";
    var agentsById = agents.reduce(function (result, item) {
      result[item.agent_id || item.id] = item;
      return result;
    }, {});
    var nodesByDevice = nodes.reduce(function (result, item) {
      var deviceId = item.host_device_id || "未绑定设备";
      if (!result[deviceId]) result[deviceId] = [];
      result[deviceId].push(item);
      return result;
    }, {});
    var deploymentsByNode = deployments.reduce(function (result, item) {
      var nodeId = item.compute_node_id || "未绑定节点";
      if (!result[nodeId]) result[nodeId] = [];
      result[nodeId].push(item);
      return result;
    }, {});
    var devicesById = devices.reduce(function (result, item) {
      result[item.device_id || item.id] = item;
      return result;
    }, {});
    nodes.forEach(function (node) {
      var deviceId = node.host_device_id || "未绑定设备";
      if (!devicesById[deviceId]) {
        devices.push({device_id: deviceId, name: deviceId, device_type: node.host_device_type || "platform", status: "unknown"});
        devicesById[deviceId] = devices[devices.length - 1];
      }
    });
    var html = devices.length ? devices.map(function (device) {
      var deviceId = device.device_id || device.id;
      var deviceNodes = nodesByDevice[deviceId] || [];
      return '<article class="deployment-device"><header><div><b>' + escapeHtml(device.name || deviceId) +
        '</b><small>' + escapeHtml(deviceId) + ' · ' + escapeHtml(device.device_type || "platform") +
        '</small></div><span>' + escapeHtml(device.status || "unknown") + '</span></header>' +
        (deviceNodes.length ? deviceNodes.map(function (node) {
          var nodeDeployments = deploymentsByNode[node.node_id] || [];
          return '<section class="deployment-compute-node"><div class="deployment-node-head"><b>' +
            escapeHtml(node.name || node.node_id) + '</b><span>' + escapeHtml(node.compute_type || "compute") +
            '</span></div><dl><div><dt>CPU</dt><dd>' + escapeHtml(node.cpu || "未配置") +
            '</dd></div><div><dt>加速器</dt><dd>' + escapeHtml(node.accelerator || "未配置") +
            '</dd></div><div><dt>内存</dt><dd>' + escapeHtml(node.memory_gb == null ? "未配置" : node.memory_gb + " GB") +
            '</dd></div><div><dt>网络</dt><dd>' + escapeHtml(node.network || "未配置") +
            '</dd></div></dl><div class="deployment-agent-list">' +
            (nodeDeployments.length ? nodeDeployments.map(function (deployment) {
              var agent = agentsById[deployment.agent_id] || {};
              return '<div class="deployment-agent"><b>' + escapeHtml(deployment.agent_id || "Agent") +
                ' · ' + escapeHtml(agent.name || "未命名 Agent") + '</b><small>' +
                escapeHtml(valueList(deployment.roles).join("；") || "未配置部署职责") +
                '</small><span>' + escapeHtml(deployment.runtime_status || "planned") + '</span></div>';
            }).join("") : '<div class="deployment-empty">该算力节点暂无 Agent 部署</div>') +
            '</div></section>';
        }).join("") : '<div class="deployment-empty">该设备暂无算力节点</div>') +
        '</article>';
    }).join("") : '<div class="empty-state">当前场景未配置 Agent—设备算力映射</div>';
    setHtmlIfChanged(root, html);
  }

  function renderBackendCapabilities() {
    var root = document.getElementById("backend-capability-map");
    if (!root) return;
    var catalog = runtimeAlgorithmCatalog || {};
    var allRows = Array.isArray(catalog.algorithms) ? catalog.algorithms : [];
    var classes = Array.isArray(catalog.algorithm_classes) ? catalog.algorithm_classes : [];
    var onnxRows = allRows.filter(function (item) {
      return item.onnx_model_provided || /_onnx$/i.test(String(item.algorithm_id || ""));
    });
    var readyRows = allRows.filter(function (item) {
      return item.runtime_status === "ready" && onnxRows.indexOf(item) < 0;
    });
    var families = {};
    allRows.forEach(function (item) { if (item.task_family) families[item.task_family] = true; });
    var activeCount = catalog.algorithm_class_count;
    if (activeCount == null) activeCount = classes.length || 20;
    var activeNode = document.getElementById("backend-active-count");
    if (activeNode) activeNode.textContent = activeCount || 0;
    var runtimeKnown = catalog.status === "ready" || catalog.status === "degraded";
    document.getElementById("backend-runnable-count").textContent = runtimeKnown ? (catalog.algorithm_package_count == null ? allRows.length : catalog.algorithm_package_count) : "未知";
    document.getElementById("backend-unavailable-count").textContent = runtimeKnown ? onnxRows.length : "未知";
    document.getElementById("backend-family-count").textContent = runtimeKnown ? Object.keys(families).length : "未知";
    var plannedNode = document.getElementById("scenario-algorithm-count");
    if (plannedNode) plannedNode.textContent = activeScenario ? asList(activeScenario.algorithm_coverage).length : "—";
    var status = document.getElementById("algorithm-runtime-status");
    if (status) {
      status.textContent = catalog.status === "ready" ? "运行时目录已连接" :
        (catalog.status === "degraded" ? "部分服务不可用" : (catalog.status === "offline" ? "算法库离线" : "检查中"));
      status.className = "status-chip " + (catalog.status === "ready" ? "success" :
        (catalog.status === "degraded" ? "warning" : (catalog.status === "offline" ? "danger" : "neutral")));
    }
    var meta = document.getElementById("algorithm-runtime-meta");
    if (meta) {
      var checkedAt = catalog.checked_at ? new Date(catalog.checked_at).toLocaleString() : "未上报";
      meta.innerHTML = '<span>来源 <b>' + escapeHtml(catalog.source || "未上报") + '</b></span>' +
        '<span>最近检查 <b>' + escapeHtml(checkedAt) + '</b></span>' +
        '<span>ONNX <b>' + escapeHtml(onnxRows.length ? "已提供 " + onnxRows.length + " 个模型包" : "未提供") + '</b></span>';
    }
    function renderAlgorithmCard(item) {
      var profile = item.model_profile || {};
      var params = profile.parameter_count_text || (profile.parameter_count == null ? "未上报" : String(profile.parameter_count));
      var ready = item.runtime_status === "ready";
      var onnxPackage = item.onnx_model_provided || /_onnx$/i.test(String(item.algorithm_id || ""));
      var stateText = onnxPackage ? "ONNX 已提供" : (ready ? "运行就绪" : "未运行");
      var localized = localizedAlgorithm(item);
      return '<article class="backend-function-card ' + (ready || onnxPackage ? "runtime-ready" : "runtime-unavailable") + '">' +
        '<header><div><b>' + escapeHtml(localized.name) + '</b></div><span>' + escapeHtml(stateText) + '</span></header>' +
        '<p>' + escapeHtml(localized.summary) + '</p>' +
        '<div class="backend-runtime-meta"><span>任务族 <b>' + escapeHtml(taskFamilyLabel(item.task_family || "未上报")) + '</b></span>' +
        '<span>版本 <b>' + escapeHtml(item.version || "未上报") + '</b></span>' +
        '<span>后端 <b>' + escapeHtml(item.backend_type || "未上报") + '</b></span>' +
        '<span>模型规模 <b>' + escapeHtml(params) + '</b></span></div></article>';
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
            escapeHtml(taskFamilyLabel(family)) + '</b><span>' + escapeHtml(items.length) + '</span></div>' +
            items.map(renderAlgorithmCard).join("") + '</div>';
        }).join("") + '</section>';
    }
    function renderClassCard(item) {
      var primary = item.primary_algorithm_ids || [];
      var auxiliary = item.auxiliary_algorithm_ids || [];
      var onnx = item.onnx_package_ids || [];
      var names = functionNameMap(catalog);
      function tags(values, type) {
        return values.map(function (value) { return '<span class="algorithm-class-tag ' + type + '">' + escapeHtml(value) + '</span>'; }).join("");
      }
      return '<article class="algorithm-class-card"><header><code>' + escapeHtml(item.requirement_id) + '</code><div><b>' + escapeHtml(item.name) +
        '</b><small>' + escapeHtml(item.category || "算法类别") + '</small></div><span class="algorithm-class-state">' + escapeHtml(item.delivery_status === "external" ? "外部能力" : "已映射") + '</span></header>' +
        '<section><label>覆盖功能</label><div class="algorithm-class-tags">' + ((item.function_points || []).length ? tags(item.function_points.map(function (id) { return functionLabel(id, names); }), "function") : '<span class="algorithm-class-empty">基础/支撑能力</span>') + '</div></section>' +
        '<footer><div><label>主实现</label><span class="algorithm-class-tags">' + (primary.length ? tags(primary, "primary") : '<span class="algorithm-class-empty">未接入</span>') + '</span></div>' +
        (auxiliary.length ? '<div><label>辅助实现</label><span class="algorithm-class-tags">' + tags(auxiliary, "supporting") + '</span></div>' : '') +
        '<div class="algorithm-class-onnx ' + (onnx.length ? "provided" : "missing") + '"><label>ONNX</label><b>' + escapeHtml(onnx.length ? "已提供" : "未提供") + '</b></div></footer></article>';
    }
    var implementationHtml = allRows.length ?
      '<section class="runtime-implementation-section"><header><div><b>运行实现包</b><small>算法库实时发现的可调度实现、模型信息及运行状态</small></div><span>' + escapeHtml(allRows.length) + '</span></header>' +
      renderGroup("可调度实现", readyRows) + renderGroup("ONNX 模型包", onnxRows) + '</section>' :
      '<div class="empty-state">' + escapeHtml(catalog.error || "后端当前没有返回算法目录") + '</div>';
    var html = classes.length ? '<section class="backend-family-section algorithm-class-section"><header><b>二十项算法类别（M01–M20）</b><span>' + escapeHtml(classes.length) + '</span></header><div class="algorithm-class-grid">' + classes.map(renderClassCard).join("") + '</div></section>' +
      '<div class="algorithm-implementation-summary"><b>实现包状态</b><span>已注册 ' + escapeHtml(catalog.registered_package_count == null ? allRows.length : catalog.registered_package_count) + ' · 业务实现 ' + escapeHtml(catalog.algorithm_package_count == null ? allRows.length : catalog.algorithm_package_count) + ' · 运行就绪 ' + escapeHtml(catalog.runnable_count || 0) + '</span></div>' + implementationHtml : implementationHtml;
    setHtmlIfChanged(root, html);
    renderFunctionAlgorithmMap(catalog);
  }

  function renderFunctionAlgorithmMap(catalog) {
    var root = document.getElementById("backend-function-map");
    if (!root) return;
    var functions = Array.isArray(catalog.operational_functions) ? catalog.operational_functions : [];
    var classes = Array.isArray(catalog.algorithm_classes) ? catalog.algorithm_classes : [];
    if (!functions.length) {
      setHtmlIfChanged(root, '<div class="empty-state">算法库尚未返回功能点目录</div>');
      return;
    }
    var grouped = {};
    var packageLinks = {};
    (Array.isArray(catalog.algorithms) ? catalog.algorithms : []).forEach(function (item) {
      (item.operational_functions || []).forEach(function (coverage) {
        var pointId = String((coverage && coverage.function_id) || "");
        if (!pointId) return;
        if (!packageLinks[pointId]) packageLinks[pointId] = [];
        packageLinks[pointId].push({
          algorithm_id: item.algorithm_id,
          name: item.display_name || item.algorithm_id,
          role: String((coverage && coverage.role) || "").toLowerCase(),
        });
      });
    });
    functions.forEach(function (point) {
      var stage = String(point.f2t2ea_stage || "other").toLowerCase();
      if (!grouped[stage]) grouped[stage] = [];
      var linked = classes.filter(function (item) { return (item.function_points || []).indexOf(point.function_id) >= 0; });
      grouped[stage].push({point: point, linked: linked, packages: packageLinks[point.function_id] || []});
    });
    var order = ["find", "fix", "track", "target", "engage", "assess"];
    function relationTags(values, className) {
      return '<span class="function-relation-tags">' + values.map(function (value) {
        return '<span class="function-relation-tag ' + className + '">' + escapeHtml(value) + '</span>';
      }).join("") + '</span>';
    }
    var html = '<section class="function-algorithm-section"><header><div><b>28 项功能点—算法关系</b></div><span>' + escapeHtml(functions.length) + '</span></header>' +
      order.filter(function (stage) { return grouped[stage]; }).map(function (stage) {
        return '<section class="function-algorithm-stage"><h3>' + escapeHtml(stageLabel(stage)) + '</h3><div class="function-algorithm-grid">' + grouped[stage].map(function (entry) {
          var classPrimary = entry.linked.map(function (item) { return item.primary_algorithm_ids || []; }).flat();
          var classAuxiliary = entry.linked.map(function (item) { return item.auxiliary_algorithm_ids || []; }).flat();
          var functionPrimary = entry.point.primary_algorithm_ids || [];
          var packagePrimary = entry.packages.filter(function (item) { return item.role === "primary" || item.role === "handoff"; }).map(function (item) { return item.algorithm_id; });
          var packageAuxiliary = entry.packages.filter(function (item) { return item.role && item.role !== "primary" && item.role !== "handoff"; }).map(function (item) { return item.algorithm_id; });
          var primary = Array.from(new Set(classPrimary.concat(functionPrimary).concat(packagePrimary)));
          var auxiliary = Array.from(new Set(classAuxiliary.concat(packageAuxiliary)));
          var primaryLabels = entry.linked.map(function (item) { return item.requirement_id + ' · ' + item.name; });
          var functionPrimaryLabels = (entry.point.primary_algorithm_names || []).map(function (name, index) {
            var id = functionPrimary[index];
            return name + '（' + id + '）';
          });
          var packageLabels = Array.from(new Set(packagePrimary.map(function (id) { return algorithmLabel(id, catalog); })));
          var mainLabels = Array.from(new Set(primaryLabels.concat(functionPrimaryLabels).concat(packageLabels)));
          return '<article><header><code>' + escapeHtml(entry.point.function_id) + '</code><b>' + escapeHtml(entry.point.name || entry.point.function_name) + '</b></header>' +
            '<div class="function-relation-line"><label>主算法</label>' + (mainLabels.length ? relationTags(mainLabels, "primary") : '<span class="function-relation-empty">未直接绑定</span>') + '</div>' +
            (primary.length ? '<div class="function-relation-line implementation"><label>实现包</label>' + relationTags(primary, "implementation") + '</div>' : '') +
            (auxiliary.length ? '<div class="function-relation-line"><label>辅助算法</label>' + relationTags(auxiliary.map(function (id) { return algorithmLabel(id, catalog); }), "supporting") + '</div>' : '') + '</article>';
        }).join("") + '</div></section>';
      }).join("") + '</section>';
    setHtmlIfChanged(root, html);
  }

  function renderKillChainRuntime(state, scenario) {
    var root = document.getElementById("kill-chain-runtime-view");
    if (!root || !scenario) return;
    var elapsed = Number(state && state.clock && state.clock.elapsed_sec || 0);
    var cueRows = Array.isArray(scenario.function_point_schedule) && scenario.function_point_schedule.length ? scenario.function_point_schedule : (Array.isArray(scenario.timeline) ? scenario.timeline : []);
    var releasedCueById = ((state && state.scenario_story && state.scenario_story.timeline) || []).reduce(function (result, cue) {
      if (cue && cue.cue_id) result[String(cue.cue_id)] = cue;
      return result;
    }, {});
    function cueForDisplay(cue) {
      var cueId = cue && cue.cue_id;
      return cueId && releasedCueById[String(cueId)] || cue || {};
    }
    function cueLabel(cue) {
      var displayCue = cueForDisplay(cue);
      return displayCue.title || (displayCue.phase ? stageLabel(displayCue.phase) : "");
    }
    var points = {};
    (scenario.function_point_coverage || []).forEach(function (point) {
      var id = point.function_id || point.id;
      if (id) points[id] = {at: Infinity, cue: {title: point.name || point.function_name || id}, events: []};
    });
    cueRows.forEach(function (cue) { (cue.function_ids || []).forEach(function (id) {
      var at = Number(cue.at_sec || 0);
      if (!points[id]) points[id] = {at: Infinity, cue: cue, events: []};
      points[id].events.push({at: at, cue: cue});
    }); });
    Object.keys(points).forEach(function (id) {
      var point = points[id];
      point.events.sort(function (left, right) { return left.at - right.at; });
      var triggered = point.events.filter(function (event) { return event.at <= elapsed; }).pop();
      if (triggered) {
        point.at = triggered.at;
        point.cue = cueForDisplay(triggered.cue);
      } else if (point.events.length) {
        point.cue = point.events[0].cue;
      }
    });
    var runtimeEvents = Array.isArray(state && state.kill_chain_events) ? state.kill_chain_events : [];
    var weapons = Array.isArray(state && state.weapons) ? state.weapons : [];
    var runtimeTriggers = scenario.function_runtime_triggers || {};
    function runtimeEvidenceTime(spec) {
      var eventName = spec && spec.event;
      var event = runtimeEvents.filter(function (row) {
        return row && String(row.type || row.event_type || "") === String(eventName || "") && Number.isFinite(Number(row.sim_time));
      })[0];
      if (event) return Number(event.sim_time);
      var fields = {
        authorized_fire_command: "launch_sim_time",
        weapon_hit: "impact_sim_time",
        damage_assessment_confirmed: "assessed_sim_time",
      };
      var field = fields[eventName];
      if (!field) return Infinity;
      var times = weapons.map(function (weapon) {
        var value = weapon && weapon[field];
        return value == null ? NaN : Number(value);
      }).filter(Number.isFinite);
      return times.length ? Math.min.apply(null, times) : Infinity;
    }
    Object.keys(runtimeTriggers).forEach(function (id) {
      if (!points[id]) return;
      var spec = runtimeTriggers[id] || {};
      var evidenceAt = runtimeEvidenceTime(spec);
      points[id].at = Number.isFinite(evidenceAt) ? evidenceAt + Number(spec.offset_sec || 0) : Infinity;
    });
    var ids = Object.keys(points).sort();
    var names = functionNameMap(runtimeAlgorithmCatalog);
    var terminal = String(state && state.clock && state.clock.lifecycle || "").toLowerCase() === "completed";
    var latestAt = Math.max.apply(null, ids.map(function (id) { return Number.isFinite(points[id].at) && points[id].at <= elapsed ? points[id].at : -1; }));
    var activeIds = terminal ? [] : ids.filter(function (id) { return latestAt >= 0 && points[id].at === latestAt; });
    var completedIds = ids.filter(function (id) { return Number.isFinite(points[id].at) && (terminal ? points[id].at <= elapsed : points[id].at < latestAt); });
    var conditionalIds = ids.filter(function (id) { return (scenario.conditional_function_points || []).indexOf(id) >= 0 && !Number.isFinite(points[id].at); });
    var pendingIds = ids.filter(function (id) { return points[id].at > elapsed && conditionalIds.indexOf(id) < 0; });
    var grouped = {};
    var coverageById = (scenario.function_point_coverage || []).reduce(function (result, point) { result[point.function_id || point.id] = point; return result; }, {});
    ids.forEach(function (id) { var raw = coverageById[id] || {}; var stage = String(raw.f2t2ea_stage || raw.category || raw.phase || "other").toLowerCase(); (grouped[stage] || (grouped[stage] = [])).push(id); });
    var hours = Math.floor(elapsed / 3600);
    var minutes = Math.floor((elapsed % 3600) / 60);
    var seconds = Math.floor(elapsed % 60);
    var currentText = terminal ? "仿真已完成" : (activeIds.map(function (id) {
      var chineseName = String(names[id] || "").split("（")[0];
      return id + (chineseName ? " · " + chineseName : "");
    }).join("、") || "—");
    var currentCueText = terminal ? "仿真已完成" : (activeIds.map(function (id) {
      return cueLabel(points[id] && points[id].cue);
    }).filter(Boolean).filter(function (value, index, array) {
      return array.indexOf(value) === index;
    }).join("、") || "等待下一剧情事件");
    root.innerHTML = '<div class="workflow-v2-summary"><span><small>当前仿真时间</small><b>T+' + escapeHtml(String(hours).padStart(2, "0") + ":" + String(minutes).padStart(2, "0") + ":" + String(seconds).padStart(2, "0")) + '</b></span><span class="current-function"><small>当前剧情事件</small><b>' + escapeHtml(currentCueText) + '</b></span><span class="current-function"><small>当前触发功能</small><b>' + escapeHtml(currentText) + '</b></span><span><small>已推进</small><b>' + escapeHtml(completedIds.length) + '</b></span><span><small>待触发</small><b>' + escapeHtml(pendingIds.length) + '</b></span><span><small>条件分支</small><b>' + escapeHtml(conditionalIds.length) + '</b></span></div>' +
      ["find", "fix", "track", "target", "engage", "assess", "other"].filter(function (stage) { return grouped[stage]; }).map(function (stage) {
        return '<section class="kill-chain-stage"><h3>' + escapeHtml(stageLabel(stage)) + '</h3><div class="kill-chain-stage-grid">' + grouped[stage].map(function (id) {
          var item = points[id];
          var conditional = conditionalIds.indexOf(id) >= 0;
          var active = activeIds.indexOf(id) >= 0;
          var status = active ? "executing" : (item.at < elapsed ? "verified" : (conditional ? "conditional" : "declared"));
          var statusText = active ? "触发中" : (item.at < elapsed ? "已推进" : (conditional ? "条件触发" : "待触发"));
          var pointName = names[id] || item.cue.title || id;
          var linkedCue = cueLabel(item.cue);
          var cueText = linkedCue ? ("剧情：" + linkedCue) : "等待对应剧情事件";
          return '<button type="button" class="workflow-function-point ' + status + '" data-kill-chain-function="' + escapeHtml(id) + '" title="' + escapeHtml(id + " · " + pointName + " · " + statusText + " · " + cueText) + '"><span>' + escapeHtml(id) + '</span><b>' + escapeHtml(pointName) + '</b><small>' + escapeHtml(stageLabel(stage)) + ' · ' + escapeHtml(statusText) + '</small><small class="kill-chain-progress-note">' + escapeHtml(cueText) + '</small></button>';
        }).join("") + '</div></section>';
      }).join("");
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
        var status = byPhase[String(element.dataset.phaseKey || "").toUpperCase()] || "pending";
        element.className = status;
      });
    });
    renderBackendCapabilities();
    renderFunctionalAgents(activeScenario);
    renderAgentDeploymentTopology(activeScenario);
    renderKillChainRuntime(state, activeScenario);
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
