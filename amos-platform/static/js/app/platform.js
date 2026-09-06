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
  var SIM_POLL_INTERVAL_MS = 3000;
  var DIRECTOR_REFRESH_INTERVAL_MS = 5000;
  var ALGORITHM_REFRESH_INTERVAL_MS = 60000;
  var running = false;
  var paused = false;
  var switchingScenario = false;
  var scenarioGeneration = 0;
  var sseReader = null;
  var sseAbortController = null;
  var pollTimer = null;
  var directorTimer = null;
  var algorithmTimer = null;
  var algorithmRefreshInFlight = null;
  var authorizationPromptKey = null;
  var authorizationTrackId = null;
  var authorizationMode = "fire";
  var authorizationAssetId = null;
  var authorizationSubmitting = false;
  var authorizationPreviousFocus = null;
  var storyHeroGeneration = 0;
  var storyHeroTarget = null;
  var activeStoryCueId = null;
  var activeWorkflowStoryContext = null;
  var speedRequestQueue = Promise.resolve();
  var speedRequestGeneration = 0;
  var pendingSpeedRequests = 0;
  var speedControlLocked = false;
  var speedResumeValue = 1;

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function setHtmlIfChanged(element, html) {
    if (element && element.innerHTML !== html) element.innerHTML = html;
  }

  function prefersReducedMotion() {
    return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function updateStoryHero(hero, media) {
    var source = media && mediaUri(media);
    if (!source) {
      storyHeroGeneration += 1;
      storyHeroTarget = null;
      hero.hidden = true;
      hero.classList.remove("story-image-changing");
      hero.removeAttribute("src");
      return;
    }
    hero.hidden = false;
    hero.alt = media.title || "当前观测资料";
    if (storyHeroTarget === source) return;
    storyHeroTarget = source;
    var generation = ++storyHeroGeneration;
    if (!hero.getAttribute("src") || prefersReducedMotion()) {
      hero.setAttribute("src", source);
      return;
    }
    var preload = new Image();
    preload.onload = function () {
      if (generation !== storyHeroGeneration) return;
      hero.classList.add("story-image-changing");
      window.setTimeout(function () {
        if (generation !== storyHeroGeneration) return;
        hero.setAttribute("src", source);
        window.requestAnimationFrame(function () {
          window.requestAnimationFrame(function () {
            if (generation === storyHeroGeneration) hero.classList.remove("story-image-changing");
          });
        });
      }, 140);
    };
    preload.onerror = function () {
      if (generation === storyHeroGeneration) hero.setAttribute("src", source);
    };
    preload.src = source;
  }

  function syncStoryMedia(items, activeMediaId) {
    var root = document.getElementById("story-media-strip");
    if (!root) return;
    var existing = {};
    root.querySelectorAll("[data-story-media-id]").forEach(function (element) {
      existing[element.dataset.storyMediaId] = element;
    });
    if (items.length) {
      var empty = root.querySelector(".empty-state");
      if (empty) empty.remove();
    }
    var retained = {};
    items.forEach(function (item) {
      var mediaId = String(item.media_id || "");
      if (!mediaId) return;
      retained[mediaId] = true;
      var button = existing[mediaId];
      if (!button) {
        button = document.createElement("button");
        button.type = "button";
        button.className = "story-media-item available story-item-enter";
        button.dataset.storyMediaId = mediaId;
        button.innerHTML = '<img alt=""><span></span>';
        button.addEventListener("animationend", function () {
          button.classList.remove("story-item-enter");
        }, {once: true});
        root.appendChild(button);
      }
      var image = button.querySelector("img");
      var source = sourceMetadata(item);
      var uri = mediaUri(item);
      if (image.getAttribute("src") !== uri) image.setAttribute("src", uri);
      image.alt = item.title || mediaId;
      var labels = {sar: "SAR", eo_ir: "EO / IR", ir: "IR", telemetry: "数据产品", radar: "RADAR"};
      button.querySelector("span").textContent =
        (labels[item.modality] || String(item.modality || "INPUT").toUpperCase()) +
        " · " + formatSimTime(source.capturedAt);
      button.classList.toggle("current", mediaId === String(activeMediaId || ""));
      button.setAttribute("aria-pressed", mediaId === String(activeMediaId || "") ? "true" : "false");
    });
    Object.keys(existing).forEach(function (mediaId) {
      if (!retained[mediaId]) existing[mediaId].remove();
    });
    if (!items.length) setHtmlIfChanged(root, '<div class="empty-state">暂无可用观测资料</div>');
  }

  function syncStoryTimeline(items, activeCue) {
    var root = document.getElementById("story-timeline");
    if (!root) return;
    var nextActiveId = activeCue && String(activeCue.cue_id || "");
    var existing = {};
    root.querySelectorAll("[data-story-cue-id]").forEach(function (element) {
      existing[element.dataset.storyCueId] = element;
    });
    if (items.length) {
      var empty = root.querySelector(".empty-state");
      if (empty) empty.remove();
    }
    var retained = {};
    items.forEach(function (item) {
      var cueId = String(item.cue_id || "");
      if (!cueId) return;
      retained[cueId] = true;
      var cue = existing[cueId];
      if (!cue) {
        cue = document.createElement("div");
        cue.className = "story-cue story-item-enter";
        cue.dataset.storyCueId = cueId;
        cue.innerHTML = '<span class="story-cue-time"></span><span class="story-cue-phase"></span>' +
          '<span class="story-cue-body"><b></b></span>';
        cue.addEventListener("animationend", function () {
          cue.classList.remove("story-item-enter");
        }, {once: true});
        root.appendChild(cue);
      }
      cue.querySelector(".story-cue-time").textContent = formatSimTime(item.at_sec);
      cue.querySelector(".story-cue-phase").textContent = item.phase || "—";
      cue.querySelector(".story-cue-body b").textContent = item.title || "阶段";
      cue.classList.toggle("active", cueId === nextActiveId);
      cue.classList.toggle("reached", cueId !== nextActiveId);
    });
    Object.keys(existing).forEach(function (cueId) {
      if (!retained[cueId]) existing[cueId].remove();
    });
    if (!items.length) setHtmlIfChanged(root, '<div class="empty-state">暂无已发生事件</div>');
    if (nextActiveId && nextActiveId !== activeStoryCueId) {
      var activeElement = existing[nextActiveId] || Array.from(
        root.querySelectorAll("[data-story-cue-id]")
      ).find(function (element) { return element.dataset.storyCueId === nextActiveId; });
      if (activeElement) activeElement.scrollIntoView({
        block: "nearest", behavior: prefersReducedMotion() ? "auto" : "smooth"
      });
    }
    activeStoryCueId = nextActiveId;
  }

  function resetStoryAnimation() {
    storyHeroGeneration += 1;
    storyHeroTarget = null;
    activeStoryCueId = null;
    ["story-media-strip", "story-timeline"].forEach(function (id) {
      var element = document.getElementById(id);
      if (element) element.replaceChildren();
    });
    var hero = document.getElementById("story-hero-image");
    if (hero) {
      hero.classList.remove("story-image-changing");
      hero.removeAttribute("src");
    }
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
    if (algorithmRefreshInFlight) return algorithmRefreshInFlight;
    algorithmRefreshInFlight = API.loadRuntimeAlgorithms().then(function (catalog) {
      Panels.updateRuntimeAlgorithms(catalog);
      return catalog;
    }).catch(function () {
      Panels.updateRuntimeAlgorithms({status: "offline", algorithms: []});
      return null;
    }).finally(function () {
      algorithmRefreshInFlight = null;
    });
    return algorithmRefreshInFlight;
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
    document.title = name === "未选择场景" ? "Simulation 场景仿真平台" : name + " · Simulation";
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
      if (card && !card.hidden) {
        card.hidden = true;
        resetStoryAnimation();
      }
      return;
    }
    var revealCard = card.hidden;
    card.hidden = false;
    if (revealCard && !prefersReducedMotion()) {
      card.classList.remove("story-card-enter");
      window.requestAnimationFrame(function () {
        card.classList.add("story-card-enter");
        card.addEventListener("animationend", function () {
          card.classList.remove("story-card-enter");
        }, {once: true});
      });
    }
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
    document.getElementById("story-current-title").textContent = cue && cue.title ||
      (activeMedia && activeMedia.title || "等待观测数据");
    var activeSource = activeMedia ? sourceMetadata(activeMedia) : null;
    document.getElementById("story-current-prompt").textContent = activeMedia
      ? (activeMedia.title || "观测资料") + " · " + activeSource.platformId + " / " + activeSource.sensorId + " · " + formatSimTime(activeSource.capturedAt)
      : "尚无可用传感器资料";
    document.getElementById("story-elapsed").textContent = formatSimTime(elapsed);
    document.getElementById("story-media-count").textContent = availableMedia.length;
    document.getElementById("story-track-count").textContent = state && (state.fused_tracks || []).length || 0;


    updateStoryHero(document.getElementById("story-hero-image"), activeMedia);
    syncStoryMedia(availableMedia, currentMediaId);
    syncStoryTimeline(reached, cue);
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
      simulation_processor: "Simulation 当前状态派生", command_system: "Commander 工作流",
      sensor_observation: "平台载荷", derived_current_state: "Simulation 当前状态派生",
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
      activeWorkflowStoryContext = null;
      authorizationPromptKey = null;
      authorizationSubmitting = false;
      closeAuthorizationDialog();
      resetEvidenceProducts();
      localStorage.setItem("amos.scenario.selected", scenarioId);
      select.value = scenarioId;
      setScenarioHeader();
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
    var clock = latestState && latestState.clock || {};
    var speedLocked = speedControlLocked || Boolean(clock.speed_locked_reason) ||
      directorStatus === "awaiting_authorization";
    startButton.disabled = running || directorBusy || !currentScenarioId;
    startButton.textContent = directorStatus === "awaiting_analysis" ? "等待分析" :
      (directorStatus === "awaiting_authorization" ? "等待授权" : (paused ? "继续" : "启动"));
    document.getElementById("btn-pause").disabled = !running;
    document.getElementById("btn-stop").disabled = !running && !directorBusy;
    document.querySelectorAll(".speed-btn").forEach(function (button) {
      button.disabled = !running || speedLocked;
    });
    var speedGroup = document.querySelector(".speed-group");
    if (speedGroup) {
      speedGroup.classList.toggle("speed-locked", speedLocked);
      speedGroup.setAttribute("aria-busy", pendingSpeedRequests > 0 ? "true" : "false");
      speedGroup.title = speedLocked
        ? "等待确认期间固定为 1×，确认完成后恢复 " +
          Number(clock.speed_resume_value || speedResumeValue || 1) + "×"
        : "仿真倍率";
    }
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
    if (!(await configureDirector())) throw new Error("导演未能重置当前场景");
    running = false;
    paused = false;
    disconnectSSE();
    if (Workflow.reset) Workflow.reset();
    onState(await API.loadSimState());
    document.getElementById("status-text").textContent = "就绪";
    document.getElementById("mode-tag").textContent = "就绪";
    updateButtons();
  }

  function renderSpeedControls(multiplier, locked, resumeValue) {
    var applied = Number(multiplier || 1);
    speedControlLocked = Boolean(locked);
    speedResumeValue = Number(resumeValue || 1);
    document.querySelectorAll(".speed-btn").forEach(function (button) {
      button.classList.toggle("speed-active", Number(button.dataset.speed) === applied);
      button.setAttribute("aria-pressed", Number(button.dataset.speed) === applied ? "true" : "false");
    });
    var speedGroup = document.querySelector(".speed-group");
    if (speedGroup) {
      speedGroup.classList.toggle("speed-locked", Boolean(locked));
      speedGroup.dataset.currentSpeed = String(applied);
      if (locked) speedGroup.dataset.resumeSpeed = String(Number(resumeValue || 1));
      else delete speedGroup.dataset.resumeSpeed;
    }
  }

  function syncSpeedFromClock(clock) {
    if (!clock) return;
    var locked = Boolean(clock.speed_locked_reason);
    if (!pendingSpeedRequests || locked) {
      renderSpeedControls(clock.speed, locked, clock.speed_resume_value);
    }
  }

  function setSpeed(multiplier) {
    var requested = Number(multiplier);
    if (!Number.isFinite(requested) || requested <= 0) {
      return Promise.reject(new Error("无效的仿真倍率"));
    }
    var generation = ++speedRequestGeneration;
    pendingSpeedRequests += 1;
    updateButtons();
    var request = speedRequestQueue.catch(function () {}).then(function () {
      return API.post("/api/v1/sim/speed", {speed: requested});
    }).then(function (response) {
      var data = response.data || {};
      if (generation === speedRequestGeneration || data.speed_locked) {
        renderSpeedControls(
          Number(data.speed == null ? requested : data.speed),
          Boolean(data.speed_locked),
          data.speed_resume_value
        );
      }
      return data;
    }).finally(function () {
      pendingSpeedRequests = Math.max(0, pendingSpeedRequests - 1);
      updateButtons();
    });
    // Serialize requests so rapid clicks always leave the backend at the last
    // selected multiplier, regardless of network response ordering.
    speedRequestQueue = request.catch(function () {});
    return request;
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

  function warningDelaySeconds() {
    var warning = latestState && latestState.engagement_warning || {};
    var policy = currentScenario && currentScenario.engagement_policy || {};
    var delay = Number(warning.delay_sec || policy.warning_delay_sec || 300);
    return Number.isFinite(delay) && delay > 0 ? Math.round(delay) : 300;
  }

  function closeAuthorizationDialog() {
    var dialog = document.getElementById("authorization-dialog");
    if (!dialog || authorizationSubmitting) return;
    dialog.hidden = true;
    dialog.classList.remove("open");
    dialog.setAttribute("aria-hidden", "true");
    authorizationTrackId = null;
    authorizationAssetId = null;
    authorizationMode = "fire";
    if (authorizationPreviousFocus && document.contains(authorizationPreviousFocus)) {
      authorizationPreviousFocus.focus();
    }
    authorizationPreviousFocus = null;
  }

  function showAuthorizationDialog(trackId, options) {
    options = options || {};
    var track = trackById(trackId);
    if (!track) return false;
    var dialog = document.getElementById("authorization-dialog");
    var confirmButton = document.getElementById("authorization-confirm");
    if (!dialog || !confirmButton) return false;
    authorizationMode = options.mode || "fire";
    authorizationTrackId = String(track.id || track.track_id);
    authorizationAssetId = options.assetId || null;
    authorizationPreviousFocus = document.activeElement;
    document.getElementById("authorization-title").textContent =
      options.title || "武器攻击授权";
    document.getElementById("authorization-phase").textContent =
      options.phase || "ENGAGE";
    document.getElementById("authorization-target-name").textContent = Panels.contactLabel
      ? Panels.contactLabel(track) : authorizationTrackId;
    document.getElementById("authorization-track-id").textContent = authorizationTrackId;
    document.getElementById("authorization-assessment").textContent =
      (track.agent_assessment && track.agent_assessment.label) || "后端已确认";
    document.getElementById("authorization-message").textContent =
      options.message || "是否授权对该目标实施打击？";
    document.getElementById("authorization-action-label").textContent =
      options.actionLabel || "拟用武器";
    document.getElementById("authorization-action-value").textContent =
      options.actionValue || "舰载反舰导弹";
    document.getElementById("authorization-error").hidden = true;
    confirmButton.disabled = false;
    confirmButton.textContent = options.confirmText || "确认打击";
    var cancelButton = document.getElementById("authorization-cancel");
    if (cancelButton) {
      cancelButton.disabled = false;
      cancelButton.textContent = options.cancelText || "暂不打击";
    }
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
      if (
        dialog && !dialog.hidden && !authorizationSubmitting &&
        authorizationMode !== "launch_follow_uav"
      ) closeAuthorizationDialog();
      return;
    }
    var stage = directorState && directorState.authorization_stage;
    // Never infer FIRE from a generic awaiting_authorization status. The live
    // clock can arrive before the director poll and that race previously opened
    // the wrong dialog (or replaced WARN before the operator could see it).
    if (stage !== "warning" && stage !== "fire") return;
    var target = authorizationTarget();
    if (!target) return;
    var checkpoint = directorState.current_checkpoint || directorState.checkpoint_id || "ENGAGE";
    var checkpointId = typeof checkpoint === "object" ? checkpoint.checkpoint_id : checkpoint;
    var runId = directorState.run_id || latestState && latestState.clock && latestState.clock.run_id || "run";
    var key = [runId, checkpointId || "ENGAGE", stage, target.id || target.track_id].join(":");
    if (key === authorizationPromptKey) return;
    if (stage === "warning") {
      if (showAuthorizationDialog(target.id || target.track_id, {
        mode: "warn",
        title: "无线电警告确认",
        phase: "警告",
        message: "武装船已进入警戒海域，是否立即发出一次无线电警告？",
        actionLabel: "警告内容",
        actionValue: "立即停止航行并驶离警戒海域",
        confirmText: "发出警告",
        cancelText: "暂不处置"
      })) authorizationPromptKey = key;
      return;
    }
    var followsWarning = directorState.authorization_not_before_sec != null;
    if (showAuthorizationDialog(target.id || target.track_id, followsWarning ? {
      mode: "fire",
      title: "武器打击确认",
      phase: "打击",
      message: "目标未回应警告，是否授权实施武器打击？",
      actionLabel: "拟用武器",
      actionValue: "舰载反舰导弹",
      confirmText: "确认打击",
      cancelText: "暂不打击"
    } : {mode: "fire"})) authorizationPromptKey = key;
  }

  function followLaunchPrompt() {
    if (!latestState) return null;
    if (latestState.follow_launch_prompt) return latestState.follow_launch_prompt;
    var prompts = latestState.follow_launch_prompts || [];
    return prompts.length ? prompts[0] : null;
  }

  function syncFollowLaunchDialog() {
    var prompt = followLaunchPrompt();
    var dialog = document.getElementById("authorization-dialog");
    if (!prompt) {
      if (
        dialog && !dialog.hidden && !authorizationSubmitting &&
        authorizationMode === "launch_follow_uav"
      ) closeAuthorizationDialog();
      return;
    }
    if (dialog && !dialog.hidden && authorizationMode !== "launch_follow_uav") return;
    var runId = latestState && latestState.clock && latestState.clock.run_id || "run";
    var key = [runId, prompt.task_id || "follow", prompt.asset_id, prompt.track_id].join(":");
    if (key === authorizationPromptKey) return;
    if (showAuthorizationDialog(prompt.track_id, {
      mode: "launch_follow_uav",
      assetId: prompt.asset_id || "UAV-CONFIRM-01",
      title: "补充侦察无人机派出确认",
      phase: "TRACK",
      message: prompt.message || "是否派出补充侦察无人机跟踪该目标？",
      actionLabel: "拟派平台",
      actionValue: prompt.asset_label || "补充侦察无人机",
      confirmText: "派出无人机",
      cancelText: "暂不派出"
    })) authorizationPromptKey = key;
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

  async function issueTargetWarning(trackId) {
    if (!trackId || authorizationSubmitting) return;
    authorizationSubmitting = true;
    var confirmButton = document.getElementById("authorization-confirm");
    var cancelButton = document.getElementById("authorization-cancel");
    if (confirmButton) {
      confirmButton.disabled = true;
      confirmButton.textContent = "警告下达中";
    }
    if (cancelButton) cancelButton.disabled = true;
    try {
      var response = await API.issueSimCommand({
        command_type: "warn",
        params: {track_id: trackId},
        authorization: {approved: true, authority: "operator"},
      });
      var result = response.data && response.data.result || {};
      var delay = Math.round(Number(result.delay_sec || warningDelaySeconds()));
      document.getElementById("status-text").textContent =
        "无线电警告已发出；继续观察 " + delay +
        " 个仿真秒，届时将弹出武器打击确认";
      authorizationSubmitting = false;
      if (cancelButton) cancelButton.disabled = false;
      closeAuthorizationDialog();
      onState(await API.loadSimState());
    } catch (error) {
      document.getElementById("status-text").textContent = "警告命令被拒绝：" + errorMessage(error);
      var errorElement = document.getElementById("authorization-error");
      if (errorElement) {
        errorElement.textContent = "后端拒绝命令：" + errorMessage(error);
        errorElement.hidden = false;
      }
    } finally {
      authorizationSubmitting = false;
      if (confirmButton) {
        confirmButton.disabled = false;
        confirmButton.textContent = "确认警告";
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
    var sameRun = incomingClock.run_id && currentClock.run_id &&
      String(incomingClock.run_id) === String(currentClock.run_id);
    var finalLifecycle = String(incomingClock.lifecycle || "").toLowerCase() === "completed" ||
      String(currentDirectorState && currentDirectorState.director_status || "").toLowerCase() === "completed";
    if (sameRun && finalLifecycle && latestState) {
      state = Object.assign({}, state);
      if (!state.assets.length && Array.isArray(latestState.assets) && latestState.assets.length) {
        state.assets = latestState.assets;
      }
      if (!state.weapons.length && Array.isArray(latestState.weapons) && latestState.weapons.length) {
        state.weapons = latestState.weapons;
      }
      if (!state.fused_tracks.length && Array.isArray(latestState.fused_tracks) && latestState.fused_tracks.length) {
        state.fused_tracks = latestState.fused_tracks;
      }
    }
    latestState = state;
    var clock = state.clock || {};
    if (currentDirectorState && clock.director_status) {
      currentDirectorState = Object.assign({}, currentDirectorState, {
        director_status: clock.director_status,
        status: clock.director_status,
        awaiting_authorization: clock.director_status === "awaiting_authorization",
        authorization_stage: Object.prototype.hasOwnProperty.call(clock, "authorization_stage")
          ? clock.authorization_stage : currentDirectorState.authorization_stage,
        authorization_not_before_sec: Object.prototype.hasOwnProperty.call(clock, "authorization_not_before_sec")
          ? clock.authorization_not_before_sec : currentDirectorState.authorization_not_before_sec,
      });
    }
    Panels.updateAll(state);
    Panels.updateWorkspace(state, currentScenario);
    Map.updateLiveState(
      state.assets || [], state.weapons || [], state.fused_tracks || [],
      Number(clock.elapsed_sec || 0), state.kill_chain_events || []
    );
    renderStory(state.scenario_story, state);
    refreshEvidenceProducts(state);
    syncSpeedFromClock(clock);
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
    if (directorStatus === "awaiting_authorization") {
      statusText = "等待操作员授权，仿真以 1× 继续运行";
      modeText = "待授权";
    } else if (clock.speed_locked_reason === "awaiting_follow_confirmation") {
      statusText = "等待无人机派遣确认，仿真以 1× 继续运行";
      modeText = "待确认";
    } else if (directorStatus === "awaiting_analysis") {
      statusText = running
        ? "后端分析中，本阶段仿真继续"
        : "后端分析中，仿真保持在阶段边界";
      modeText = "分析中";
    }
    document.getElementById("status-text").textContent = statusText;
    document.getElementById("mode-tag").textContent = modeText;
    Workflow.syncRun(clock.run_id);
    updateButtons();
    syncAuthorizationDialog(currentDirectorState);
    syncFollowLaunchDialog();
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
    pollTimer = setInterval(function () { API.loadSimState().then(onState).catch(function () {}); }, SIM_POLL_INTERVAL_MS);
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
    var algorithmEvidence = document.getElementById("workflow-algorithm-evidence");
    if (algorithmEvidence) algorithmEvidence.hidden = true;
    if (!view) {
      activeWorkflowStoryContext = null;
      if (latestStory) renderStory(latestStory, latestState);
    }
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
    updateButtons();
  }

  function handleDirectorSubmission(state) {
    var checkpoint = state && state.current_checkpoint;
    var submission = checkpoint && typeof checkpoint === "object" && checkpoint.submission || null;
    var workflowId = submission && submission.workflow_id;
    var analysisStatus = checkpoint && typeof checkpoint === "object" && checkpoint.analysis_status;
    var checkpointKey = checkpoint && typeof checkpoint === "object"
      ? [checkpoint.checkpoint_id, analysisStatus, workflowId].join(":") : null;
    if (!checkpointKey || checkpointKey === handledDirectorCheckpoint) return;
    handledDirectorCheckpoint = checkpointKey;
    if (workflowId) {
      if (String(workflowId) !== String(directorWorkflowId || "")) {
        directorWorkflowId = String(workflowId);
        if (Workflow.track) Workflow.track(directorWorkflowId, state.run_id);
      }
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
    var sameScenario = currentDirectorState &&
      String(currentDirectorState.scenario_id) === String(currentScenarioId);
    var branches = currentScenario && currentScenario.expected_branches || [];
    var firstBranch = branches.length && (branches[0].branch_id || branches[0].id);
    return {
      scenario_id: currentScenarioId,
      mode: sameScenario && currentDirectorState.mode || "integration",
      branch: sameScenario && currentDirectorState.branch || currentScenario && currentScenario.default_branch || firstBranch || "standard",
      seed: sameScenario && currentDirectorState.seed != null
        ? currentDirectorState.seed : currentScenario && currentScenario.default_seed,
    };
  }

  async function configureDirector() {
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
      return true;
    } catch (error) {
      renderDirectorState(null);
      return false;
    }
  }

  async function runDirectorAction(action, button) {
    if (!currentScenarioId) return;
    button.disabled = true;
    try {
      var desired = directorConfiguration();
      var configuredForSelection = currentDirectorState &&
        String(currentDirectorState.scenario_id) === String(desired.scenario_id) &&
        String(currentDirectorState.mode) === String(desired.mode) &&
        String(currentDirectorState.branch) === String(desired.branch) &&
        Number(currentDirectorState.seed) === Number(desired.seed);
      if (!configuredForSelection && !(await configureDirector())) return;
      var response = await API.runDirectorAction(action, {scenario_id: currentScenarioId});
      var state = response.data || response;
      renderDirectorState(state);
      handleDirectorSubmission(state);
      onState(await API.loadSimState());
      if (action === "start_auto") connectSSE();
      return state;
    } catch (error) {
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
      if (authorizationMode === "launch_follow_uav") {
        issueFollowUavLaunch(authorizationTrackId, authorizationAssetId);
      } else if (authorizationMode === "warn") {
        issueTargetWarning(authorizationTrackId);
      } else {
        issueWeaponAttack(authorizationTrackId);
      }
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
    document.querySelectorAll("[data-report-format]").forEach(function (button) {
      button.addEventListener("click", function () { downloadRunReport(this.dataset.reportFormat); });
    });
    document.addEventListener("amos:workflow-view", function (event) { reflectWorkflowEvidence(event.detail || null); });
    document.addEventListener("amos:workflow-activity-context", function (event) {
      activeWorkflowStoryContext = event.detail || null;
      if (latestStory) renderStory(latestStory, latestState);
    });
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
    directorTimer = setInterval(refreshDirectorState, DIRECTOR_REFRESH_INTERVAL_MS);
    algorithmTimer = setInterval(refreshRuntimeAlgorithms, ALGORITHM_REFRESH_INTERVAL_MS);
  }

  async function issueFollowUavLaunch(trackId, assetId) {
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
        command_type: "launch_follow_uav",
        params: {
          track_id: trackId,
          asset_id: assetId || "UAV-CONFIRM-01",
        },
        authorization: {approved: true, authority: "operator"},
      });
      document.getElementById("status-text").textContent = "补充侦察无人机已派出：" +
        (response.data && response.data.result && response.data.result.asset_id || assetId || "UAV-CONFIRM-01");
      authorizationSubmitting = false;
      if (cancelButton) cancelButton.disabled = false;
      closeAuthorizationDialog();
      onState(await API.loadSimState());
    } catch (error) {
      document.getElementById("status-text").textContent = "无人机派出命令被拒绝：" + errorMessage(error);
      var errorElement = document.getElementById("authorization-error");
      if (errorElement) {
        errorElement.textContent = "后端拒绝命令：" + errorMessage(error);
        errorElement.hidden = false;
      }
    } finally {
      authorizationSubmitting = false;
      if (confirmButton) {
        confirmButton.disabled = false;
        confirmButton.textContent = "派出无人机";
      }
      if (cancelButton) cancelButton.disabled = false;
    }
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
