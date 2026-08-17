/* AMOS dashboard controller: multi-scenario simulation, director and backend evidence. */

window.Platform = (function () {
  var API = window.PlatformAPI;
  var Map = window.PlatformMap;
  var Panels = window.PlatformPanels;
  var Workflow = window.PlatformWorkflow;
  var currentScenarioId = null;
  var currentScenario = null;
  var scenarioCatalog = [];
  var latestStory = null;
  var latestState = null;
  var currentMediaId = null;
  var evidenceProducts = {};
  var evidenceManifestKey = null;
  var evidenceManifestGeneration = 0;
  var evidenceRunId = null;
  var supportData = null;
  var currentDirectorState = null;
  var directorWorkflowId = null;
  var handledDirectorCheckpoint = null;
  var running = false;
  var paused = false;
  var switchingScenario = false;
  var scenarioGeneration = 0;
  var sseReader = null;
  var sseAbortController = null;
  var pollTimer = null;
  var directorTimer = null;
  var algorithmTimer = null;
  var authorizationPromptKey = null;
  var authorizationTrackId = null;
  var authorizationSubmitting = false;
  var authorizationPreviousFocus = null;

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function setHtmlIfChanged(element, html) {
    if (element && element.innerHTML !== html) element.innerHTML = html;
  }

  function errorMessage(error) {
    if (!error) return "操作失败";
    return error.message || error.detail || String(error);
  }

  function directorOwnsLiveUpdates(state) {
    if (!state) return false;
    var status = state.director_status || state.status;
    return Boolean(state.auto_running) || [
      "auto_running", "awaiting_analysis", "awaiting_authorization"
    ].indexOf(status) >= 0;
  }

  function ensureLiveUpdates() {
    if (!pollTimer && !sseAbortController) connectSSE();
  }

  function refreshRuntimeAlgorithms() {
    return API.loadRuntimeAlgorithms().then(function (catalog) {
      Panels.updateRuntimeAlgorithms(catalog);
      return catalog;
    }).catch(function () {
      Panels.updateRuntimeAlgorithms({status: "offline", algorithms: []});
      return null;
    });
  }

  function formatSimTime(value) {
    var total = Math.max(0, Math.floor(Number(value) || 0));
    var hours = Math.floor(total / 3600);
    var minutes = Math.floor((total % 3600) / 60);
    var seconds = total % 60;
    return "T+" + String(hours).padStart(2, "0") + ":" +
      String(minutes).padStart(2, "0") + ":" + String(seconds).padStart(2, "0");
  }

  function mediaUri(item) {
    var dynamicProduct = item && evidenceProducts[item.media_id];
    return dynamicProduct && dynamicProduct.uri || item && (item.uri || item.media_uri) || "";
  }

  function sourceMetadata(item) {
    var product = item && evidenceProducts[item.media_id] || {};
    var sensorId = product.sensor_instance_id || item && item.sensor_instance_id;
    var platformId = product.platform_id || item && item.platform_id;
    if ((!platformId || !sensorId) && item && item.sensor_id) {
      var parts = String(item.sensor_id).split("/");
      platformId = platformId || parts.shift();
      sensorId = sensorId || parts.join("/");
    }
    var productData = item && item.product_data || {};
    var observations = Array.isArray(productData.observations) ? productData.observations : [];
    function measurementSummary(key) {
      var values = observations.map(function (observation) { return Number(observation[key]); })
        .filter(function (value) { return Number.isFinite(value); });
      if (!values.length) return null;
      if (values.length === 1) return Math.round(values[0] * 100) / 100;
      return [Math.round(Math.min.apply(null, values) * 100) / 100,
        Math.round(Math.max.apply(null, values) * 100) / 100];
    }
    var provenance = item && item.capture_provenance || {};
    var consumer = item && item.consumer_context || {};
    return {
      platformId: platformId || "未提供",
      sensorId: sensorId || "未提供",
      capturedAt: product.captured_at_sec != null ? product.captured_at_sec :
        item && (item.captured_at_sim_time != null ? item.captured_at_sim_time : item.at_sec),
      bearing: product.bearing_summary_deg != null ? product.bearing_summary_deg : measurementSummary("bearing_deg"),
      range: product.range_summary_nm != null ? product.range_summary_nm : measurementSummary("range_nm"),
      slantRange: measurementSummary("slant_range_nm"),
      elevation: measurementSummary("elevation_deg"),
      rendererType: product.renderer_type,
      captureId: product.capture_id || item && item.capture_id,
      snapshotAt: product.snapshot_at_sec,
      sourceKind: provenance.capability_source_kind || provenance.source_kind || "未提供",
      platformPose: item && item.platform_pose || {},
      sensorPose: item && item.sensor_pose || {},
      sensorConfig: item && item.sensor_config || {},
      observationCount: observations.length || item && (item.observation_ids || []).length || 0,
      plannedRoles: Array.isArray(consumer.functional_role_ids) ? consumer.functional_role_ids : [],
      plannedModels: Array.isArray(consumer.model_requirement_ids) ? consumer.model_requirement_ids : [],
      executionEvidence: consumer.execution_evidence,
    };
  }

  function formatSummary(value, suffix) {
    if (value == null || value === "") return "未提供";
    if (Array.isArray(value)) {
      return value.length ? value.map(function (entry) { return String(entry); }).join("–") + suffix : "未提供";
    }
    return String(value) + suffix;
  }

  function formatResolution(value) {
    if (Array.isArray(value) && value.length === 2) {
      return String(value[0]) + "×" + String(value[1]) + " px";
    }
    return value == null || value === "" ? "未提供" : String(value);
  }

  function resetEvidenceProducts() {
    evidenceProducts = {};
    evidenceManifestKey = null;
    evidenceRunId = null;
    evidenceManifestGeneration += 1;
  }

  function refreshEvidenceProducts(state) {
    var clock = state && state.clock || {};
    var runId = clock.run_id;
    var story = state && state.scenario_story || {};
    var mediaIds = (story.media_cues || []).map(function (item) { return String(item.media_id || ""); })
      .filter(Boolean).sort();
    if (!runId) {
      resetEvidenceProducts();
      return;
    }
    if (evidenceRunId && String(evidenceRunId) !== String(runId)) resetEvidenceProducts();
    evidenceRunId = String(runId);
    var key = String(runId) + ":" + mediaIds.join(",");
    if (key === evidenceManifestKey) return;
    evidenceManifestKey = key;
    var generation = ++evidenceManifestGeneration;
    API.loadEvidenceProducts().then(function (manifest) {
      if (generation !== evidenceManifestGeneration || !manifest || String(manifest.run_id) !== String(runId)) return;
      evidenceProducts = (manifest.products || []).reduce(function (result, product) {
        if (product && product.media_id && product.uri) result[product.media_id] = product;
        return result;
      }, {});
      if (latestState && latestState.clock && String(latestState.clock.run_id) === String(runId)) {
        renderStory(latestState.scenario_story, latestState);
      }
    }).catch(function () {
      // Static media remains available; absence of the endpoint never creates
      // substitute evidence or changes verification state.
      evidenceProducts = {};
    });
  }

  function scenarioSummary(id) {
    return scenarioCatalog.find(function (item) { return String(item.id) === String(id); }) || {};
  }

  function setScenarioHeader() {
    var summary = scenarioSummary(currentScenarioId);
    var name = currentScenario && currentScenario.name || summary.name || "未选择场景";
    var heading = document.getElementById("scenario-context");
    if (heading) {
      heading.textContent = name;
      heading.title = name;
    }
    document.title = name === "未选择场景" ? "AMOS 场景仿真平台" : name + " · AMOS";
  }

  function renderStoryAnalysis(analysis) {
    var status = document.getElementById("story-agent-status");
    if (!status) return;
    var labels = {
      completed: "已完成", running: "分析中", submitted: "已提交",
      failed: "失败", stale_run: "上一轮结果", integrity_error: "校验失败",
    };
    var state = analysis && String(analysis.status || "").toLowerCase();
    status.textContent = labels[state] || state || "尚未开始";
  }

  function renderStory(story, state) {
    var card = document.getElementById("scenario-story-card");
    var definition = story && story.timeline && story.timeline.length ? story : currentScenario;
    if (!card || !definition || !(definition.timeline || []).length) {
      if (card) card.hidden = true;
      return;
    }
    card.hidden = false;
    latestStory = definition;
    if (state) latestState = state;
    var elapsed = Number(state && state.clock && state.clock.elapsed_sec || 0);
    var reached = (definition.timeline || []).filter(function (cue) {
      return Number(cue.at_sec || 0) <= elapsed;
    });
    var availableMedia = (definition.media_cues || []).filter(function (item) {
      return Number(item.at_sec || 0) <= elapsed;
    });
    var cue = reached[reached.length - 1] || null;
    var cueMedia = cue ? availableMedia.filter(function (item) {
      return (cue.media_ids || []).indexOf(item.media_id) >= 0;
    }) : [];
    var activeMedia = cueMedia[cueMedia.length - 1] || availableMedia[availableMedia.length - 1] || null;
    currentMediaId = activeMedia && activeMedia.media_id;

    document.getElementById("story-title").textContent = "场景态势";
    document.getElementById("story-phase-badge").textContent = cue && cue.phase || "READY";
    document.getElementById("story-current-title").textContent = activeMedia && activeMedia.title ||
      (cue && cue.title || "等待观测数据");
    var activeSource = activeMedia ? sourceMetadata(activeMedia) : null;
    document.getElementById("story-current-prompt").textContent = activeMedia
      ? activeSource.platformId + " / " + activeSource.sensorId + " · " + formatSimTime(activeSource.capturedAt)
      : "尚无可用传感器资料";
    document.getElementById("story-elapsed").textContent = formatSimTime(elapsed);
    document.getElementById("story-media-count").textContent = availableMedia.length;
    document.getElementById("story-track-count").textContent = state && (state.fused_tracks || []).length || 0;


    var hero = document.getElementById("story-hero-image");
    hero.hidden = !activeMedia;
    if (activeMedia) {
      var heroSource = mediaUri(activeMedia);
      if (hero.getAttribute("src") !== heroSource) hero.setAttribute("src", heroSource);
      hero.alt = activeMedia.title || "当前观测资料";
    } else {
      hero.removeAttribute("src");
    }

    var labels = {sar: "SAR", eo_ir: "EO / IR", ir: "IR", telemetry: "数据产品", radar: "RADAR"};
    setHtmlIfChanged(document.getElementById("story-media-strip"), availableMedia.length
      ? availableMedia.map(function (item) {
        var source = sourceMetadata(item);
        return '<button type="button" class="story-media-item available" data-story-media-id="' +
          escapeHtml(item.media_id) + '"><img src="' + escapeHtml(mediaUri(item)) + '" alt="' +
          escapeHtml(item.title || item.media_id) + '"><span>' +
          escapeHtml(labels[item.modality] || String(item.modality || "INPUT").toUpperCase()) +
          " · " + escapeHtml(formatSimTime(source.capturedAt)) + "</span></button>";
      }).join("")
      : '<div class="empty-state">暂无可用观测资料</div>');

    setHtmlIfChanged(document.getElementById("story-timeline"), reached.length
      ? reached.map(function (item) {
        var active = cue && item.cue_id === cue.cue_id ? "active" : "reached";
        return '<div class="story-cue ' + active + '"><span class="story-cue-time">' +
          escapeHtml(formatSimTime(item.at_sec)) + '</span><span class="story-cue-phase">' +
          escapeHtml(item.phase || "—") + '</span><span class="story-cue-body"><b>' +
          escapeHtml(item.title || "阶段") + "</b></span></div>";
      }).join("")
      : '<div class="empty-state">暂无已发生事件</div>');
    renderStoryAnalysis(definition.agent_analysis || null);
  }

  function showMedia(mediaId) {
    var item = latestStory && (latestStory.media_cues || []).find(function (entry) {
      return entry.media_id === mediaId;
    });
    var elapsed = Number(latestState && latestState.clock && latestState.clock.elapsed_sec || 0);
    if (!item || Number(item.at_sec || 0) > elapsed) return;
    var source = sourceMetadata(item);
    var sourceLabels = {
      asset: "平台载荷", external_source: "外部预采集资料",
      simulation_processor: "AMOS 当前状态派生", command_system: "Commander 工作流",
      sensor_observation: "平台载荷", derived_current_state: "AMOS 当前状态派生",
      external_precollected: "外部预采集资料", commander_workflow: "Commander 工作流"
    };
    var pose = source.platformPose || {};
    var sensorPose = source.sensorPose || {};
    var sensorConfig = source.sensorConfig || {};
    var location = Number.isFinite(Number(pose.lat)) && Number.isFinite(Number(pose.lon))
      ? Number(pose.lat).toFixed(5) + ", " + Number(pose.lon).toFixed(5) : "未提供";
    document.getElementById("media-lightbox-title").textContent = item.title || "观测资料";
    document.getElementById("media-lightbox-image").src = mediaUri(item);
    document.getElementById("media-lightbox-meta").innerHTML =
      "<b>来源平台：</b>" + escapeHtml(source.platformId) + "　" +
      "<b>传感器：</b>" + escapeHtml(source.sensorId) + "　" +
      "<b>采集时刻：</b>" + escapeHtml(formatSimTime(source.capturedAt)) + "<br>" +
      "<b>产品来源：</b>" + escapeHtml(sourceLabels[source.sourceKind] || source.sourceKind) + "　" +
      "<b>观测记录：</b>" + escapeHtml(source.observationCount) + "　" +
      "<b>Capture ID：</b>" + escapeHtml(source.captureId || "未提供") + "<br>" +
      "<b>平台位置：</b>" + escapeHtml(location) + "　" +
      "<b>高度：</b>" + escapeHtml(formatSummary(pose.alt_ft, " ft")) + "　" +
      "<b>航向：</b>" + escapeHtml(formatSummary(pose.heading_deg, "°")) + "　" +
      "<b>传感器方位：</b>" + escapeHtml(formatSummary(sensorPose.azimuth_deg, "°")) + "　" +
      "<b>俯角：</b>" + escapeHtml(formatSummary(sensorPose.depression_angle_deg, "°")) + "<br>" +
      "<b>方位摘要：</b>" + escapeHtml(formatSummary(source.bearing, "°")) + "　" +
      "<b>地面距离：</b>" + escapeHtml(formatSummary(source.range, " NM")) + "　" +
      "<b>斜距：</b>" + escapeHtml(formatSummary(source.slantRange, " NM")) + "<br>" +
      "<b>水平 / 垂直视场：</b>" + escapeHtml(formatSummary(sensorConfig.fov_deg, "°")) + " / " +
      escapeHtml(formatSummary(sensorConfig.vertical_fov_deg, "°")) + "　" +
      "<b>量程：</b>" + escapeHtml(formatSummary(sensorConfig.range_nm, " NM")) + "　" +
      "<b>产品分辨率：</b>" + escapeHtml(formatResolution(sensorConfig.resolution)) + "<br>" +
      "<b>计划消费角色：</b>" + escapeHtml(source.plannedRoles.join(" / ") || "未声明") + "　" +
      "<b>计划模型需求：</b>" + escapeHtml(source.plannedModels.join(" / ") || "未声明") + "　" +
      "<b>执行状态：</b>" + escapeHtml(source.executionEvidence === "backend_trace_required" ? "等待后端 trace 验证" : "未提供") + "<br>" +
      escapeHtml(item.caption || item.text || "");
    var lightbox = document.getElementById("media-lightbox");
    lightbox.classList.add("open");
    lightbox.setAttribute("aria-hidden", "false");
  }

  function closeMedia() {
    var lightbox = document.getElementById("media-lightbox");
    lightbox.classList.remove("open");
    lightbox.setAttribute("aria-hidden", "true");
  }

  function openKnowledgeGraph() {
    var dialog = document.getElementById("knowledge-graph-dialog");
    var frame = document.getElementById("knowledge-graph-frame");
    if (!frame.getAttribute("src")) frame.setAttribute("src", frame.dataset.src);
    dialog.classList.add("open");
    dialog.setAttribute("aria-hidden", "false");
  }

  function closeKnowledgeGraph() {
    var dialog = document.getElementById("knowledge-graph-dialog");
    dialog.classList.remove("open");
    dialog.setAttribute("aria-hidden", "true");
  }

  function branchOptions() {
    var configured = currentScenario && currentScenario.expected_branches || scenarioSummary(currentScenarioId).expected_branches || [];
    var defaults = [
      {id: "standard", name: "标准运行"},
      {id: "low_compute", name: "低算力"},
      {id: "communication_interference", name: "通信干扰"},
      {id: "agent_failure", name: "Agent 故障"},
      {id: "low_score_replan", name: "低评分重规划"},
    ];
    if (!Array.isArray(configured) || !configured.length) return defaults;
    return configured.map(function (item) {
      return typeof item === "string" ? {id: item, name: item} : {
        id: item.id || item.branch_id || item.mode,
        name: item.name || item.title || item.id || item.branch_id,
      };
    }).filter(function (item) { return item.id; });
  }

  function updateDirectorOptions() {
    var branch = document.getElementById("director-branch-select");
    var selected = branch.value;
    branch.innerHTML = branchOptions().map(function (item) {
      return '<option value="' + escapeHtml(item.id) + '">' + escapeHtml(item.name) + '</option>';
    }).join("");
    if (Array.from(branch.options).some(function (option) { return option.value === selected; })) branch.value = selected;
    var seed = currentScenario && (currentScenario.default_seed != null
      ? currentScenario.default_seed : currentScenario.demo_controls && currentScenario.demo_controls.default_seed);
    if (seed != null) document.getElementById("director-seed").value = seed;
  }

  async function switchScenario(scenarioId, options) {
    options = options || {};
    if (!scenarioId || switchingScenario) return;
    switchingScenario = true;
    var generation = ++scenarioGeneration;
    var select = document.getElementById("scenario-select");
    select.disabled = true;
    try {
      if (options.reset && latestState && latestState.clock && latestState.clock.run_id) {
        disconnectSSE();
        if (running) await API.post("/api/v1/sim/stop", {});
      }
      var scenario = await API.loadScenario(scenarioId);
      if (generation !== scenarioGeneration) return;
      currentScenarioId = scenarioId;
      currentScenario = scenario;
      latestState = null;
      latestStory = null;
      currentMediaId = null;
      authorizationPromptKey = null;
      authorizationSubmitting = false;
      closeAuthorizationDialog();
      resetEvidenceProducts();
      localStorage.setItem("amos.scenario.selected", scenarioId);
      select.value = scenarioId;
      setScenarioHeader();
      updateDirectorOptions();
      Map.loadScenario({
        assets: currentScenario.assets || [],
        theater: currentScenario.theater,
        map_display: currentScenario.map_display || {},
      });
      if (supportData && supportData.sensor_models) {
        Map.renderAllSensorFootprints(currentScenario.assets || [], supportData.sensor_models);
      }
      renderStory(currentScenario, null);
      Panels.updateWorkspace({}, currentScenario);
      if (options.reset) {
        var response = await API.configureDirector(directorConfiguration());
        running = false;
        paused = false;
        if (Workflow.reset) Workflow.reset();
        directorWorkflowId = null;
        handledDirectorCheckpoint = null;
        renderDirectorState(response.data || response);
        onState(await API.loadSimState());
      } else if (options.state && options.state.clock && options.state.clock.scenario_id === scenarioId) {
        onState(options.state);
      }
      document.getElementById("status-text").textContent = "场景就绪";
      var layerState = Map.getLayerState();
      ["sensors", "ao"].forEach(function (name) {
        document.getElementById("btn-toggle-" + name).classList.toggle("layer-active", Boolean(layerState[name]));
      });
      refreshDirectorState();
    } catch (error) {
      document.getElementById("status-text").textContent = "场景加载失败：" + errorMessage(error);
      throw error;
    } finally {
      if (generation === scenarioGeneration) {
        switchingScenario = false;
        select.disabled = false;
      }
    }
  }

  function renderScenarioCatalog(items) {
    scenarioCatalog = Array.isArray(items) ? items : [];
    var select = document.getElementById("scenario-select");
    select.innerHTML = scenarioCatalog.length ? scenarioCatalog.map(function (item) {
      return '<option value="' + escapeHtml(item.id) + '">' + escapeHtml(item.name || item.id) + '</option>';
    }).join("") : '<option value="">未配置场景</option>';
    select.disabled = !scenarioCatalog.length;
  }

  function updateButtons() {
    var startButton = document.getElementById("btn-start");
    var directorStatus = currentDirectorState && currentDirectorState.director_status;
    var directorBusy = ["auto_running", "awaiting_analysis", "awaiting_authorization"].indexOf(directorStatus) >= 0;
    startButton.disabled = running || directorBusy || !currentScenarioId;
    startButton.textContent = directorStatus === "awaiting_analysis" ? "等待分析" :
      (directorStatus === "awaiting_authorization" ? "等待授权" : (paused ? "继续" : "启动"));
    document.getElementById("btn-pause").disabled = !running;
    document.getElementById("btn-stop").disabled = !running && !directorBusy;
    document.querySelectorAll(".speed-btn").forEach(function (button) { button.disabled = !running; });
  }

  async function startSim() {
    if (running || !currentScenarioId) return;
    var directorState = await runDirectorAction("start_auto", document.getElementById("btn-start"));
    if (!directorState) throw new Error("导演未能启动自动流程");
    var speed = Number(currentScenario && currentScenario.demo_controls && currentScenario.demo_controls.recommended_speed || 1);
    await setSpeed(speed);
    onState(await API.loadSimState());
    connectSSE();
  }

  async function pauseSim() {
    var directorState = await runDirectorAction("stop_auto", document.getElementById("btn-pause"));
    if (!directorState) throw new Error("导演未能暂停自动流程");
    running = false;
    paused = true;
    disconnectSSE();
    onState(await API.loadSimState());
  }

  async function stopSim() {
    if (currentDirectorState && ["completed", "unconfigured"].indexOf(currentDirectorState.director_status) < 0) {
      await API.runDirectorAction("stop_auto", {scenario_id: currentScenarioId});
    }
    await API.post("/api/v1/sim/stop", {});
    running = false;
    paused = false;
    disconnectSSE();
    onState(await API.loadSimState());
    document.getElementById("status-text").textContent = "已停止";
    document.getElementById("mode-tag").textContent = "停止";
    updateButtons();
  }

  async function resetSim() {
    if (!currentScenarioId) return;
    if (!(await configureDirector(false))) throw new Error("导演未能重置当前场景");
    running = false;
    paused = false;
    disconnectSSE();
    if (Workflow.reset) Workflow.reset();
    onState(await API.loadSimState());
    document.getElementById("status-text").textContent = "就绪";
    document.getElementById("mode-tag").textContent = "就绪";
    updateButtons();
  }

  async function setSpeed(multiplier) {
    var response = await API.post("/api/v1/sim/speed", {speed: multiplier});
    var applied = Number(response.data && response.data.speed || multiplier);
    document.querySelectorAll(".speed-btn").forEach(function (button) {
      button.classList.toggle("speed-active", Number(button.dataset.speed) === applied);
    });
    return response.data;
  }

  function trackById(trackId) {
    return (latestState && latestState.fused_tracks || []).find(function (track) {
      return String(track.id || track.track_id || "") === String(trackId || "");
    }) || null;
  }

  function authorizationTarget() {
    return (latestState && latestState.fused_tracks || []).find(function (track) {
      var assessment = track.agent_assessment || {};
      var classification = String(track.classification || "").toUpperCase();
      return track.engagement_eligible === true && assessment.status === "confirmed" &&
        !/FISHING|CIVILIAN|MERCHANT/.test(classification);
    }) || null;
  }

  function closeAuthorizationDialog() {
    var dialog = document.getElementById("authorization-dialog");
    if (!dialog || authorizationSubmitting) return;
    dialog.hidden = true;
    dialog.classList.remove("open");
    dialog.setAttribute("aria-hidden", "true");
    authorizationTrackId = null;
    if (authorizationPreviousFocus && document.contains(authorizationPreviousFocus)) {
      authorizationPreviousFocus.focus();
    }
    authorizationPreviousFocus = null;
  }

  function showAuthorizationDialog(trackId) {
    var track = trackById(trackId);
    if (!track) return false;
    var dialog = document.getElementById("authorization-dialog");
    var confirmButton = document.getElementById("authorization-confirm");
    if (!dialog || !confirmButton) return false;
    authorizationTrackId = String(track.id || track.track_id);
    authorizationPreviousFocus = document.activeElement;
    document.getElementById("authorization-target-name").textContent = Panels.contactLabel
      ? Panels.contactLabel(track) : authorizationTrackId;
    document.getElementById("authorization-track-id").textContent = authorizationTrackId;
    document.getElementById("authorization-assessment").textContent =
      (track.agent_assessment && track.agent_assessment.label) || "后端已确认";
    document.getElementById("authorization-message").textContent = "是否授权对该目标实施打击？";
    document.getElementById("authorization-error").hidden = true;
    confirmButton.disabled = false;
    confirmButton.textContent = "确认打击";
    dialog.hidden = false;
    dialog.classList.add("open");
    dialog.setAttribute("aria-hidden", "false");
    window.requestAnimationFrame(function () { confirmButton.focus(); });
    return true;
  }

  function syncAuthorizationDialog(directorState) {
    var status = directorState && (directorState.director_status || directorState.status);
    var awaiting = status === "awaiting_authorization" || Boolean(directorState && directorState.awaiting_authorization);
    if (!awaiting) {
      var dialog = document.getElementById("authorization-dialog");
      if (dialog && !dialog.hidden && !authorizationSubmitting) closeAuthorizationDialog();
      return;
    }
    var target = authorizationTarget();
    if (!target) return;
    var checkpoint = directorState.current_checkpoint || directorState.checkpoint_id || "ENGAGE";
    var checkpointId = typeof checkpoint === "object" ? checkpoint.checkpoint_id : checkpoint;
    var runId = directorState.run_id || latestState && latestState.clock && latestState.clock.run_id || "run";
    var key = [runId, checkpointId || "ENGAGE", target.id || target.track_id].join(":");
    if (key === authorizationPromptKey) return;
    authorizationPromptKey = key;
    showAuthorizationDialog(target.id || target.track_id);
  }

  async function issueWeaponAttack(trackId) {
    if (!trackId || authorizationSubmitting) return;
    authorizationSubmitting = true;
    var confirmButton = document.getElementById("authorization-confirm");
    var cancelButton = document.getElementById("authorization-cancel");
    if (confirmButton) {
      confirmButton.disabled = true;
      confirmButton.textContent = "命令下达中";
    }
    if (cancelButton) cancelButton.disabled = true;
    try {
      var response = await API.issueSimCommand({
        command_type: "fire",
        params: {
          track_id: trackId,
          asset_id: "ESCORT-01",
          weapon_name: "舰载反舰导弹",
        },
        authorization: {approved: true, authority: "operator"},
      });
      document.getElementById("status-text").textContent = "攻击命令已执行：" +
        (response.data && response.data.result && response.data.result.weapon_id || trackId);
      authorizationSubmitting = false;
      if (cancelButton) cancelButton.disabled = false;
      closeAuthorizationDialog();
      onState(await API.loadSimState());
    } catch (error) {
      document.getElementById("status-text").textContent = "攻击命令被拒绝：" + errorMessage(error);
      var errorElement = document.getElementById("authorization-error");
      if (errorElement) {
        errorElement.textContent = "后端拒绝命令：" + errorMessage(error);
        errorElement.hidden = false;
      }
    } finally {
      authorizationSubmitting = false;
      if (confirmButton) {
        confirmButton.disabled = false;
        confirmButton.textContent = "确认打击";
      }
      if (cancelButton) cancelButton.disabled = false;
    }
  }

  function onState(state) {
    if (
      !state || !state.clock ||
      !Array.isArray(state.assets) ||
      !Array.isArray(state.weapons) ||
      !Array.isArray(state.fused_tracks)
    ) return;
    var incomingClock = state.clock || {};
    var currentClock = latestState && latestState.clock || {};
    if (
      incomingClock.run_id && currentClock.run_id &&
      String(incomingClock.run_id) === String(currentClock.run_id) &&
      Number(incomingClock.elapsed_sec || 0) + 0.001 < Number(currentClock.elapsed_sec || 0)
    ) return;
    latestState = state;
    Panels.updateAll(state);
    Panels.updateWorkspace(state, currentScenario);
    Map.updateLiveState(state.assets || [], state.weapons || [], state.fused_tracks || []);
    renderStory(state.scenario_story, state);
    refreshEvidenceProducts(state);
    var clock = state.clock || {};
    updateReportControls(clock.run_id);
    running = Boolean(clock.running);
    var lifecycle = clock.lifecycle || (running ? "running" : "ready");
    paused = lifecycle === "paused";
    var statusLabels = {
      running: "仿真运行中", completed: "仿真完成", paused: "仿真已暂停",
      stopped: "仿真已停止", error: "仿真异常停止", ready: "场景就绪", empty: "就绪"
    };
    var directorStatus = currentDirectorState && currentDirectorState.director_status;
    var statusText = statusLabels[lifecycle] || "就绪";
    var modeText = ({
      running: "运行中", completed: "完成", paused: "暂停", stopped: "停止",
      error: "异常", ready: "就绪", empty: "就绪",
    })[lifecycle] || "就绪";
    if (directorStatus === "awaiting_analysis") {
      statusText = running
        ? "后端分析中，本阶段仿真继续"
        : "后端分析中，仿真保持在阶段边界";
      modeText = "分析中";
    } else if (directorStatus === "awaiting_authorization") {
      statusText = "等待操作员授权，仿真保持在 ENGAGE 入口";
      modeText = "待授权";
    }
    document.getElementById("status-text").textContent = statusText;
    document.getElementById("mode-tag").textContent = modeText;
    Workflow.syncRun(clock.run_id);
    updateButtons();
    syncAuthorizationDialog(currentDirectorState);
    if (!running && !directorOwnsLiveUpdates(currentDirectorState)) disconnectSSE();
  }

  function updateReportControls(runId) {
    var available = Boolean(runId);
    document.querySelectorAll("[data-report-format]").forEach(function (button) {
      button.disabled = !available;
    });
    var status = document.getElementById("run-report-status");
    if (!status) return;
    status.textContent = available ? "RUN " + runId : "尚无运行记录";
    status.title = available ? String(runId) : "";
    status.className = "status-chip " + (available ? "info" : "neutral");
  }

  function downloadRunReport(format) {
    var runId = latestState && latestState.clock && latestState.clock.run_id;
    if (!runId) {
      updateReportControls(null);
      return;
    }
    var link = document.createElement("a");
    link.href = API.runReportUrl(runId, format);
    link.hidden = true;
    document.body.appendChild(link);
    link.click();
    link.remove();
  }

  function connectSSE() {
    if (pollTimer || sseAbortController) return;
    sseAbortController = new AbortController();
    pollTimer = setInterval(function () { API.loadSimState().then(onState).catch(function () {}); }, 1000);
    fetch("/api/v1/sim/stream", {
      signal: sseAbortController.signal,
      headers: {Accept: "text/event-stream"},
    }).then(function (response) {
      if (!response.ok) throw new Error("SSE unavailable");
      sseReader = response.body.getReader();
      readSSE();
    }).catch(function () {});
  }

  async function readSSE() {
    var decoder = new TextDecoder();
    var buffer = "";
    try {
      while (sseReader) {
        var result = await sseReader.read();
        if (result.done) break;
        buffer += decoder.decode(result.value, {stream: true});
        var blocks = buffer.split("\n\n");
        buffer = blocks.pop() || "";
        blocks.forEach(function (block) {
          var eventLine = block.split("\n").find(function (line) { return line.indexOf("event: ") === 0; });
          var eventName = eventLine ? eventLine.slice(7).trim() : "message";
          if (eventName !== "sim_state") return;
          var dataLine = block.split("\n").find(function (line) { return line.indexOf("data: ") === 0; });
          if (dataLine) {
            try { onState(JSON.parse(dataLine.slice(6))); } catch (error) {}
          }
        });
      }
    } catch (error) {}
  }

  function disconnectSSE() {
    if (sseAbortController) sseAbortController.abort();
    sseAbortController = null;
    sseReader = null;
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
  }

  function toggleLayer(name) {
    var visible = Map.toggleLayer(name);
    document.getElementById("btn-toggle-" + name).classList.toggle("layer-active", visible);
  }

  function selectWorkspace(name) {
    if (!document.querySelector('[data-workspace-tab="' + name + '"]')) return;
    document.querySelectorAll("[data-workspace-tab]").forEach(function (button) {
      var selected = button.dataset.workspaceTab === name;
      button.classList.toggle("active", selected);
      button.setAttribute("aria-selected", selected ? "true" : "false");
    });
    document.querySelectorAll("[data-workspace-panel]").forEach(function (panel) {
      var selected = panel.dataset.workspacePanel === name;
      panel.hidden = !selected;
      panel.classList.toggle("active", selected);
    });
    sessionStorage.setItem("amos.workspace.tab", name);
  }

  function reflectWorkflowEvidence(view) {
    var hasView = Boolean(view && view.schema_version === "amos.workflow-view.v2");
    var algorithmEvidence = document.getElementById("workflow-algorithm-evidence");
    var functionEvidence = document.getElementById("workflow-function-evidence");
    if (algorithmEvidence) algorithmEvidence.hidden = !hasView;
    if (functionEvidence) functionEvidence.hidden = !hasView;
  }

  function initWorkspaceResize() {
    var root = document.documentElement;
    var resizer = document.getElementById("workspace-resizer");
    var stored = Number(localStorage.getItem("amos.workspace.width"));
    function bounds() {
      return {min: Math.min(420, window.innerWidth * 0.42), max: window.innerWidth * 0.58};
    }
    function apply(width, persist) {
      var limit = bounds();
      var value = Math.max(limit.min, Math.min(limit.max, width));
      root.style.setProperty("--workspace-width", Math.round(value) + "px");
      if (persist) localStorage.setItem("amos.workspace.width", Math.round(value));
      if (Map.invalidateSize) Map.invalidateSize();
    }
    if (stored > 0) apply(stored, false);
    resizer.addEventListener("pointerdown", function (event) {
      event.preventDefault();
      resizer.setPointerCapture(event.pointerId);
      resizer.classList.add("dragging");
    });
    resizer.addEventListener("pointermove", function (event) {
      if (!resizer.hasPointerCapture(event.pointerId)) return;
      apply(window.innerWidth - event.clientX, false);
    });
    resizer.addEventListener("pointerup", function (event) {
      if (!resizer.hasPointerCapture(event.pointerId)) return;
      resizer.releasePointerCapture(event.pointerId);
      resizer.classList.remove("dragging");
      var width = document.getElementById("dash-panel").getBoundingClientRect().width;
      apply(width, true);
    });
    resizer.addEventListener("keydown", function (event) {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      var current = document.getElementById("dash-panel").getBoundingClientRect().width;
      apply(current + (event.key === "ArrowLeft" ? 24 : -24), true);
    });
    window.addEventListener("resize", function () {
      if (window.innerWidth > 800) apply(document.getElementById("dash-panel").getBoundingClientRect().width, false);
    });
  }

  function renderDirectorState(state) {
    currentDirectorState = state && Object.keys(state).length ? state : null;
    var available = state && Object.keys(state).length;
    var status = state && (state.director_status || state.status);
    var statusLabels = {
      unconfigured: "未配置", configured: "已配置", running: "运行中", paused: "已暂停",
      auto_running: "自动演示", checkpoint_reached: "检查点已到达",
      awaiting_analysis: "等待后端分析", awaiting_authorization: "等待人工授权",
      completed: "已完成", error: "异常",
    };
    var statusText = statusLabels[status] || status;
    var badge = document.getElementById("director-status-badge");
    badge.textContent = available ? (statusText || "状态未上报") : "导演接口不可用";
    badge.className = "status-chip " +
      (["running", "auto_running"].indexOf(status) >= 0 ? "info" :
        (status === "completed" ? "success" : (status === "error" ? "danger" :
          (status === "awaiting_analysis" ? "warning" : "neutral"))));
    var checkpoint = state && (state.current_checkpoint || state.checkpoint_id);
    var checkpointLabel = checkpoint && typeof checkpoint === "object"
      ? (checkpoint.title || checkpoint.checkpoint_id || "检查点已到达")
      : checkpoint;
    document.getElementById("director-checkpoint").textContent = checkpointLabel || (available ? "尚未到达" : "接口不可用");
    document.getElementById("director-state").textContent = statusText || "未上报";
    var branchId = state && (state.branch || state.run_branch);
    var branch = branchOptions().find(function (item) { return String(item.id) === String(branchId); });
    document.getElementById("director-branch").textContent = branch && branch.name || branchId || "未上报";
    document.getElementById("director-active-seed").textContent = state && state.seed != null ? state.seed : "未上报";
    updateButtons();
  }

  function handleDirectorSubmission(state) {
    var checkpoint = state && state.current_checkpoint;
    var submission = checkpoint && typeof checkpoint === "object" && checkpoint.submission || null;
    var workflowId = submission && submission.workflow_id;
    var analysisStatus = checkpoint && typeof checkpoint === "object" && checkpoint.analysis_status;
    var message = document.getElementById("director-message");
    var checkpointKey = checkpoint && typeof checkpoint === "object"
      ? [checkpoint.checkpoint_id, analysisStatus, workflowId].join(":") : null;
    if (!checkpointKey || checkpointKey === handledDirectorCheckpoint) return;
    handledDirectorCheckpoint = checkpointKey;
    if (workflowId) {
      if (String(workflowId) !== String(directorWorkflowId || "")) {
        directorWorkflowId = String(workflowId);
        if (Workflow.track) Workflow.track(directorWorkflowId, state.run_id);
      }
      message.hidden = false;
      message.textContent = analysisStatus === "completed"
        ? "检查点分析已由后端完成并通过验证。"
        : (analysisStatus === "failed"
            ? "检查点分析失败：" + errorMessage(checkpoint.analysis_error)
            : (analysisStatus === "backend_unreachable"
                ? "分析服务暂时不可达，导演保持暂停并继续重试。"
                : "检查点分析正在后端执行。"));
      return;
    }
    if (["submission_failed", "submission_unverified"].indexOf(analysisStatus) >= 0) {
      message.hidden = false;
      message.textContent = analysisStatus === "submission_failed"
        ? "检查点分析任务提交失败，未生成工作流结果。"
        : "检查点未返回 workflow_id，分析状态尚未验证。";
    } else if (analysisStatus === "submission_unavailable") {
      message.hidden = false;
      message.textContent = "检查点已到达，但自动分析服务未配置。";
    }
  }

  async function refreshDirectorState() {
    try {
      var state = await API.getDirectorState();
      if (state && state.scenario_id && currentScenarioId && String(state.scenario_id) !== String(currentScenarioId)) {
        renderDirectorState(null);
        return;
      }
      var simulationRunId = latestState && latestState.clock && latestState.clock.run_id;
      if (state && state.run_id && simulationRunId && String(state.run_id) !== String(simulationRunId)) {
        renderDirectorState(null);
        return;
      }
      renderDirectorState(state);
      handleDirectorSubmission(state);
      syncAuthorizationDialog(state);
      if (directorOwnsLiveUpdates(state)) ensureLiveUpdates();
    } catch (error) {
      renderDirectorState(null);
    }
  }

  function directorConfiguration() {
    var seed = Number(document.getElementById("director-seed").value);
    return {
      scenario_id: currentScenarioId,
      mode: document.getElementById("director-mode-select").value,
      branch: document.getElementById("director-branch-select").value,
      seed: Number.isFinite(seed) ? seed : null,
    };
  }

  async function configureDirector(showMessage) {
    var message = document.getElementById("director-message");
    try {
      var response = await API.configureDirector(directorConfiguration());
      var state = response.data || response;
      directorWorkflowId = null;
      handledDirectorCheckpoint = null;
      authorizationPromptKey = null;
      authorizationSubmitting = false;
      closeAuthorizationDialog();
      renderDirectorState(state);
      onState(await API.loadSimState());
      if (showMessage) {
        message.hidden = false;
        message.textContent = "导演配置已应用；执行状态以服务端事件记录为准。";
      }
      return true;
    } catch (error) {
      message.hidden = false;
      message.textContent = error.status === 404 ? "导演接口不存在" : "导演配置失败：" + errorMessage(error);
      renderDirectorState(null);
      return false;
    }
  }

  async function runDirectorAction(action, button) {
    if (!currentScenarioId) return;
    var message = document.getElementById("director-message");
    button.disabled = true;
    try {
      var desired = directorConfiguration();
      var configuredForSelection = currentDirectorState &&
        String(currentDirectorState.scenario_id) === String(desired.scenario_id) &&
        String(currentDirectorState.mode) === String(desired.mode) &&
        String(currentDirectorState.branch) === String(desired.branch) &&
        Number(currentDirectorState.seed) === Number(desired.seed);
      if (!configuredForSelection && !(await configureDirector(false))) return;
      var response = await API.runDirectorAction(action, {scenario_id: currentScenarioId});
      var state = response.data || response;
      renderDirectorState(state);
      handleDirectorSubmission(state);
      if (!(state.current_checkpoint && state.current_checkpoint.analysis_status)) message.hidden = true;
      onState(await API.loadSimState());
      if (action === "start_auto") connectSSE();
      return state;
    } catch (error) {
      message.hidden = false;
      message.textContent = error.status === 404 ? "导演接口不存在" : "导演操作失败：" + errorMessage(error);
      return null;
    } finally {
      button.disabled = false;
      updateButtons();
    }
  }

  function bindControls() {
    document.getElementById("btn-start").addEventListener("click", function () { startSim().catch(showControlError); });
    document.getElementById("btn-pause").addEventListener("click", function () { pauseSim().catch(showControlError); });
    document.getElementById("btn-stop").addEventListener("click", function () { stopSim().catch(showControlError); });
    document.getElementById("btn-reset").addEventListener("click", function () { resetSim().catch(showControlError); });
    document.getElementById("scenario-select").addEventListener("change", function () {
      switchScenario(this.value, {reset: true}).catch(function () {});
    });
    document.getElementById("btn-focus-map").addEventListener("click", Map.focusScenarioView);
    ["sensors", "ao"].forEach(function (name) {
      document.getElementById("btn-toggle-" + name).addEventListener("click", function () { toggleLayer(name); });
    });
    document.getElementById("media-lightbox-close").addEventListener("click", closeMedia);
    document.getElementById("media-lightbox").addEventListener("click", function (event) { if (event.target === this) closeMedia(); });
    document.getElementById("btn-open-knowledge-graph").addEventListener("click", openKnowledgeGraph);
    document.getElementById("knowledge-graph-close").addEventListener("click", closeKnowledgeGraph);
    document.getElementById("knowledge-graph-dialog").addEventListener("click", function (event) {
      if (event.target === this) closeKnowledgeGraph();
    });
    document.getElementById("story-hero-image").addEventListener("click", function () { if (currentMediaId) showMedia(currentMediaId); });
    document.getElementById("story-media-strip").addEventListener("click", function (event) {
      var button = event.target.closest("[data-story-media-id]");
      if (button) showMedia(button.dataset.storyMediaId);
    });
    document.getElementById("authorization-cancel").addEventListener("click", closeAuthorizationDialog);
    document.getElementById("authorization-confirm").addEventListener("click", function () {
      issueWeaponAttack(authorizationTrackId);
    });
    document.getElementById("authorization-dialog").addEventListener("click", function (event) {
      if (event.target === this) closeAuthorizationDialog();
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && document.getElementById("knowledge-graph-dialog").classList.contains("open")) {
        closeKnowledgeGraph();
      }
      if (event.key === "Escape" && !document.getElementById("authorization-dialog").hidden) {
        closeAuthorizationDialog();
      }
    });
    document.querySelectorAll(".speed-btn").forEach(function (button) {
      button.addEventListener("click", function () { setSpeed(Number(this.dataset.speed)).catch(showControlError); });
    });
    document.querySelectorAll("[data-workspace-tab]").forEach(function (button) {
      button.addEventListener("click", function () { selectWorkspace(this.dataset.workspaceTab); });
    });
    document.getElementById("btn-director-configure").addEventListener("click", function () { configureDirector(true); });
    document.querySelectorAll("[data-report-format]").forEach(function (button) {
      button.addEventListener("click", function () { downloadRunReport(this.dataset.reportFormat); });
    });
    document.addEventListener("amos:workflow-view", function (event) { reflectWorkflowEvidence(event.detail || null); });
  }

  function showControlError(error) {
    document.getElementById("status-text").textContent = "操作失败：" + errorMessage(error);
  }

  async function init() {
    var query = new URLSearchParams(window.location.search);
    Map.init();
    Panels.updateAll({});
    initWorkspaceResize();
    selectWorkspace(query.get("tab") || sessionStorage.getItem("amos.workspace.tab") || "situation");
    var initial = await Promise.all([
      API.loadScenarioSupport().catch(function () { return {}; }),
      API.loadScenarios(),
      API.loadSimState().catch(function () { return {}; }),
      API.loadRuntimeAlgorithms().catch(function () { return {status: "offline", algorithms: []}; }),
    ]);
    supportData = initial[0] || {};
    Panels.updateRuntimeAlgorithms(initial[3]);
    var catalogPayload = initial[1] || {};
    renderScenarioCatalog(catalogPayload.scenarios || catalogPayload);
    Workflow.init(API, {
      getScenarioId: function () { return currentScenarioId; },
      hasSimulationState: function () { return Boolean(latestState && latestState.clock && latestState.clock.run_id); },
      getRunId: function () { return latestState && latestState.clock && latestState.clock.run_id || null; },
      onAnalysis: renderStoryAnalysis,
      onTerminal: function () { API.loadSimState().then(onState).catch(function () {}); },
    });
    if (!scenarioCatalog.length) throw new Error("服务端未配置可用场景");
    var restoredState = initial[2] || {};
    var restoredId = restoredState.clock && restoredState.clock.scenario_id;
    var savedId = localStorage.getItem("amos.scenario.selected");
    var selectedId = [query.get("scenario"), restoredId, savedId, scenarioCatalog[0].id].find(function (id) {
      return id && scenarioCatalog.some(function (item) { return String(item.id) === String(id); });
    });
    var stateMatchesSelection = restoredId && String(restoredId) === String(selectedId);
    await switchScenario(selectedId, {
      reset: !stateMatchesSelection,
      state: stateMatchesSelection ? restoredState : null,
    });
    updateButtons();
    if (running) connectSSE();
    refreshDirectorState();
    directorTimer = setInterval(refreshDirectorState, 3000);
    algorithmTimer = setInterval(refreshRuntimeAlgorithms, 10000);
  }

  document.addEventListener("DOMContentLoaded", function () {
    bindControls();
    init().catch(function (error) {
      document.getElementById("status-text").textContent = "初始化失败：" + errorMessage(error);
    });
  });

  return {
    startSim: startSim,
    pauseSim: pauseSim,
    stopSim: stopSim,
    resetSim: resetSim,
    switchScenario: switchScenario,
    toggleLayer: toggleLayer,
    setSpeed: setSpeed,
    selectWorkspace: selectWorkspace,
    showStoryMedia: showMedia,
  };
})();
