/* Causal tactical map: current positions plus already-observed history only. */

window.PlatformMap = (function () {
  var map = null;
  var baseMapLayer = null;
  var terrainDetailLayer = null;
  var mapDetailLayer = null;
  var mapLabelsVisible = readStoredMapLabels();
  var ownMarkers = {};
  var weaponMarkers = {};
  var weaponImpactLayers = {};
  var processedWeaponImpacts = {};
  var destroyedImpactMarkers = {};
  var trackMarkers = {};
  var ownTrails = {};
  var trackTrails = {};
  var sensorLayers = {};
  var coordinationLayers = {};
  var protectedLayers = [];
  var spaceGroundTrackLayers = [];
  var spaceOperationsElement = null;
  var sensorLayerSignatures = {};
  var sensorPoseSignatures = {};
  var sensorModels = {};
  var aoLayer = null;
  var gridLayers = [];
  var scenarioView = null;
  var scenarioSurface = "maritime";
  var layerState = {
    terrain: true, hillshade: true, contours: true, sensors: false, coordination: true, ao: true,
  };
  var reliefLayers = {};
  var reliefManifest = null;
  var reliefManifestPath = null;
  var reliefLegend = null;
  var ownLabelLayoutFrame = null;
  var SymbolLibrary = window.TacticalSymbols;

  function readStoredMapLabels() {
    try {
      return window.localStorage.getItem("amos.map.labels") !== "hidden";
    } catch (error) {
      return true;
    }
  }

  function createBaseMapLayer() {
    if (!map) return;
    if (baseMapLayer) map.removeLayer(baseMapLayer);
    if (mapDetailLayer) map.removeLayer(mapDetailLayer);
    var options = {
      url: "/static/tiles/taiwan-southeast-tactical.pmtiles",
      lang: "zh",
      minZoom: 5,
      // Fetch only bundled z9 data, but redraw vectors at the display zoom.
      // Leaflet maxNativeZoom would enlarge rasterized labels as well.
      maxDataZoom: 9,
      maxZoom: 14,
      noWrap: true,
      attribution: 'Protomaps · © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    };
    baseMapLayer = L.tileLayer("/static/tiles/natural-terrain/{z}/{x}/{y}.webp?v=20260911a", {
      minZoom: 5, maxNativeZoom: 9, maxZoom: 14,
      bounds: [[8, 105], [35, 135]], noWrap: true,
      attribution: '<a href="https://www.naturalearthdata.com/">Natural Earth II</a>',
    }).addTo(map);
    if (terrainDetailLayer) map.removeLayer(terrainDetailLayer);
    terrainDetailLayer = L.tileLayer("/static/tiles/natural-terrain/detail/{z}/{x}/{y}.webp?v=20260911a", {
      minZoom: 8, maxNativeZoom: 11, maxZoom: 14,
      bounds: [[17, 119], [25, 124]], noWrap: true,
      attribution: '<a href="https://registry.opendata.aws/terrain-tiles/">Mapzen Terrain</a> · USGS · NOAA/NCEI',
    });
    mapDetailLayer = protomapsL.leafletLayer(Object.assign({}, options, {
      pane: "mapDetailPane",
      paintRules: [], labelRules: [{
        dataLayer: "places",
        symbolizer: new protomapsL.CenteredTextSymbolizer({
          labelProps: ["name:zh", "name"], font: '500 11px "Microsoft YaHei", sans-serif',
          fill: "#e0e6db", stroke: "#2b403f", width: 2,
        }),
      }],
    }));
    if (mapLabelsVisible) mapDetailLayer.addTo(map);
    if (baseMapLayer.bringToBack) baseMapLayer.bringToBack();
    var container = map.getContainer();
    container.classList.add("map-style-natural");
    document.documentElement.setAttribute("data-map-style", "natural");
    updateNaturalTerrain();
  }

  function updateNaturalTerrain() {
    if (!map || !terrainDetailLayer) return;
    // Switch only when the complete viewport fits inside the detailed pack.
    // This avoids partially covered rectangles or missing tiles at its edge.
    var detailed = map.getZoom() >= 8 && L.latLngBounds([[17, 119], [25, 124]]).contains(map.getBounds());
    if (detailed && !map.hasLayer(terrainDetailLayer)) terrainDetailLayer.addTo(map);
    else if (!detailed && map.hasLayer(terrainDetailLayer)) map.removeLayer(terrainDetailLayer);
  }

  function toggleMapLabels() {
    mapLabelsVisible = !mapLabelsVisible;
    try { window.localStorage.setItem("amos.map.labels", mapLabelsVisible ? "visible" : "hidden"); } catch (error) {}
    if (mapDetailLayer && map) {
      if (mapLabelsVisible) mapDetailLayer.addTo(map);
      else map.removeLayer(mapDetailLayer);
    }
    return mapLabelsVisible;
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function icon(kind, heading, size) {
    var actualSize = size || 26;
    return L.divIcon({
      className: "rotating-marker marker-" + SymbolLibrary.affiliation(kind),
      html: '<div class="marker-rotator" style="width:' + actualSize + "px;height:" + actualSize + 'px">' +
        SymbolLibrary.svg(kind, heading) + "</div>",
      iconSize: [actualSize, actualSize],
      iconAnchor: [actualSize / 2, actualSize / 2],
    });
  }

  function ownKind(asset) {
    return SymbolLibrary.ownKind(asset);
  }

  function trackKind(track) {
    var assessment = track.agent_assessment || {};
    var classification = String(track.classification || "").toUpperCase();
    var domain = String(track.domain_hint || track.domain || "").toLowerCase();
    if (assessment.damage_state === "destroyed" || assessment.engagement_status === "destroyed") {
      return "destroyed";
    }
    if (assessment.damage_state === "impact_pending" || assessment.engagement_status === "pending_assessment") {
      return "impact";
    }
    if (assessment.source && /FISHING|CIVILIAN|MERCHANT/.test(classification)) return "civilianSurface";
    var prefix = assessment.status === "confirmed" && /high|hostile|threat|威胁|敌/i.test(
      String(assessment.level || assessment.label || "")
    ) ? "hostile" : "unknown";
    if (/COASTAL_MISSILE_SITE|MISSILE_SITE|MISSILE_BATTERY/.test(classification)) return prefix + "MissileSite";
    if (/ground|land/.test(domain)) return prefix + "Ground";
    if (/air|aviation/.test(domain)) return prefix + "Air";
    return prefix + "Surface";
  }

  function trackAssessmentLabel(track) {
    var assessment = track.agent_assessment || {};
    var classification = String(track.classification || "").toUpperCase();
    if (assessment.damage_state === "destroyed" || assessment.engagement_status === "destroyed") {
      return "已击毁 · 威胁解除";
    }
    if (assessment.damage_state === "impact_pending" || assessment.engagement_status === "pending_assessment") {
      return "导弹命中 · 待毁伤评估";
    }
    if (assessment.source && /FISHING|CIVILIAN|MERCHANT/.test(classification)) {
      return assessment.behavior_label || "民用禁射";
    }
    return assessment.source ? (assessment.label || "已评估") : "未分类";
  }

  function trackLabelClass(kind) {
    if (kind === "destroyed") return "destroyed-label";
    if (kind === "impact") return "impact-label";
    var affiliation = SymbolLibrary.affiliation(kind);
    if (affiliation === "hostile") return "threat-label";
    if (affiliation === "civilian") return "civilian-label";
    return "unknown-label";
  }

  function trackTrailStyle(kind) {
    if (kind === "destroyed") return {color: "#9aa7ad", opacity: 0.20, weight: 1.1, dashArray: "2 6"};
    if (kind === "impact") return {color: "#d2ad76", opacity: 0.30, weight: 1.25, dashArray: "3 5"};
    var affiliation = SymbolLibrary.affiliation(kind);
    if (affiliation === "hostile") return {color: "#c58f89", opacity: 0.28, weight: 1.2, dashArray: "3 6"};
    if (affiliation === "civilian") return {color: "#8db7ab", opacity: 0.22, weight: 1.1, dashArray: "2 7"};
    return {color: "#b9aa7d", opacity: 0.24, weight: 1.15, dashArray: "3 7"};
  }

  function sensorCapabilityHtml(asset) {
    var entries = (asset.sensors || []).map(function (name) {
      var key = String(name).replace(/ /g, "_").toUpperCase();
      var spec = sensorModels[key] || sensorModels[name];
      if (!spec || !Number(spec.range_nm)) return escapeHtml(name);
      return escapeHtml(name) + "（" + escapeHtml(spec.range_nm) + " NM / " +
        escapeHtml(Number(spec.fov_deg || 360)) + "°）";
    });
    return entries.length ? "<br>传感器 " + entries.join("；") : "";
  }

  function assetPopupHtml(asset) {
    var id = asset.asset_id || asset.id;
    var memberCount = Number(asset.swarm_size || asset.member_count || 0);
    var kind = ownKind(asset);
    var memberUnit = asset.domain === "maritime" ? "艘" : "架";
    var formationLabel = kind === "uavSwarm" ? "蜂群规模" : "编队规模";
    return "<b>" + escapeHtml(id) + "</b><br>" + escapeHtml(asset.role || "") +
      (asset.behavior_label ? "<br>行为 " + escapeHtml(asset.behavior_label) : "") +
      "<br>航向 " + escapeHtml(Math.round(Number(asset.heading || asset.heading_deg || 0))) + "° · " +
      escapeHtml(Math.round(Number(asset.speed_kts || 0))) + " kt" +
      (/^(air|space)$/.test(String(asset.domain || "")) ? "<br>高度 " +
        escapeHtml(Math.round(Number((asset.position || {}).alt_ft || asset.alt_ft || 0))) + " ft" : "") +
      (memberCount > 1 ? "<br>" + formationLabel + " " + escapeHtml(memberCount) + " " + memberUnit : "") +
      sensorCapabilityHtml(asset);
  }

  function renderProtectedAssets(protectedAssets, policyBufferM) {
    protectedLayers.forEach(removeLayer);
    protectedLayers = [];
    (protectedAssets || []).forEach(function (asset) {
      var lat = Number(asset.lat == null ? (asset.position || {}).lat : asset.lat);
      var lng = Number(asset.lng == null ? (asset.lon == null ? (asset.position || {}).lng : asset.lon) : asset.lng);
      if (!Number.isFinite(lat) || !Number.isFinite(lng)) return;
      var coreRadius = Math.max(250, Number(asset.protection_radius_m || 0));
      var radius = coreRadius + Number(policyBufferM || 0);
      var zone = L.circle([lat, lng], {
        radius: radius, color: "#65d6a1", weight: 1.5, opacity: 0.75,
        fillColor: "#1f6b50", fillOpacity: 0.12, dashArray: "7 5",
        interactive: true,
      }).addTo(map);
      zone.bindTooltip(escapeHtml(asset.asset_name || "民用保护区") + " · 保护半径及武器安全缓冲", {sticky: true});
      var coreZone = L.circle([lat, lng], {
        radius: coreRadius, color: "#8af0bd", weight: 1.2, opacity: 0.9,
        fillColor: "#2b8a64", fillOpacity: 0.08, interactive: false,
      }).addTo(map);
      var center = L.circleMarker([lat, lng], {
        radius: 4, color: "#8af0bd", weight: 2, fillColor: "#173b2d", fillOpacity: 1,
      }).addTo(map);
      center.bindTooltip(escapeHtml(asset.asset_name || "民用保护区"), {
        permanent: true, direction: "left", className: "map-resource-label civilian-label",
      });
      protectedLayers.push(zone, coreZone, center);
    });
  }

  function formatSpaceTime(value) {
    var total = Math.max(0, Math.round(Number(value || 0)));
    var hours = Math.floor(total / 3600);
    var minutes = Math.floor((total % 3600) / 60);
    var seconds = total % 60;
    return (hours ? String(hours).padStart(2, "0") + ":" : "") +
      String(minutes).padStart(2, "0") + ":" + String(seconds).padStart(2, "0");
  }

  function spacePassState(pass, elapsedSec) {
    var start = Number(pass.access_start_sec || 0);
    var end = Number(pass.access_end_sec || start);
    if (elapsedSec < start) return {label: "预计 T+" + formatSpaceTime(start), phase: "scheduled"};
    if (elapsedSec <= end) return {label: "过境中 · 剩余 " + formatSpaceTime(end - elapsedSec), phase: "active"};
    return {label: "已离场 · 产品已下传", phase: "complete"};
  }

  function renderSpaceOperations(elapsedSec) {
    if (!spaceOperationsElement) spaceOperationsElement = document.getElementById("space-operations-strip");
    if (!spaceOperationsElement) return;
    var operations = scenarioView && scenarioView.spaceOperations;
    if (!operations) {
      spaceOperationsElement.hidden = true;
      spaceOperationsElement.replaceChildren();
      return;
    }
    var elapsed = Math.max(0, Number(elapsedSec || 0));
    var passes = operations.passes || [];
    var selectedPass = passes.find(function (item) {
      return elapsed >= Number(item.access_start_sec || 0) && elapsed <= Number(item.access_end_sec || 0);
    }) || passes.find(function (item) {
      return elapsed < Number(item.access_start_sec || 0);
    }) || passes[passes.length - 1];
    var passState = selectedPass ? spacePassState(selectedPass, elapsed) : {label: "无计划过境", phase: "idle"};
    var products = operations.intelligence_products || [];
    var selectedProduct = products.filter(function (item) {
      return elapsed >= Number(item.captured_at_sec || 0);
    }).slice(-1)[0] || products[0];
    var productState = "待获取";
    if (selectedProduct && elapsed >= Number(selectedProduct.received_at_sec || selectedProduct.captured_at_sec || 0)) {
      var remaining = Number(selectedProduct.valid_until_sec || 0) - elapsed;
      productState = remaining > 0 ? "有效 · " + formatSpaceTime(remaining) : "需空基复核";
    } else if (selectedProduct && elapsed >= Number(selectedProduct.captured_at_sec || 0)) {
      productState = "处理中/下传";
    }
    var relay = operations.relay || {};
    spaceOperationsElement.innerHTML =
      '<div class="space-ops-head"><b>' + escapeHtml(operations.title || "空天支援") +
      '</b><span>' + escapeHtml(relay.label || "中继链路在线") + '</span></div>' +
      '<div class="space-ops-row"><strong>' + escapeHtml((selectedPass || {}).label || "侦察星过境") +
      '</strong><em>' + escapeHtml(passState.label) + '</em></div>' +
      '<div class="space-ops-row space-product"><strong>' + escapeHtml((selectedProduct || {}).label || "情报产品") +
      '</strong><em>' + escapeHtml(productState) + '</em></div>' +
      '<p class="space-ops-note">' + escapeHtml(operations.note || "卫星离场不删除已下传产品；超出有效期后必须由其他传感器复核。") + '</p>';
    spaceOperationsElement.hidden = false;
  }

  function renderSpaceGroundTracks(tracks) {
    spaceGroundTrackLayers.forEach(removeLayer);
    spaceGroundTrackLayers = [];
    (tracks || []).forEach(function (track) {
      var points = (track.points || []).map(function (point) {
        return [Number(point.lat), Number(point.lng == null ? point.lon : point.lng)];
      }).filter(function (point) {
        return Number.isFinite(point[0]) && Number.isFinite(point[1]);
      });
      if (points.length < 2) return;
      var line = L.polyline(points, {
        color: track.color || "#ad9fc5",
        weight: Number(track.weight || 1.1),
        opacity: Number(track.opacity == null ? 0.30 : track.opacity),
        dashArray: track.dash_array || "5 9",
        interactive: true,
      }).addTo(map);
      line.bindTooltip(escapeHtml(track.label || "卫星预测星下轨迹"), {sticky: true});
      line._amosTrackConfig = track;
      spaceGroundTrackLayers.push(line);
    });
  }

  function updateSpaceGroundTracks(elapsedSec) {
    var elapsed = Math.max(0, Number(elapsedSec || 0));
    spaceGroundTrackLayers.forEach(function (line) {
      var track = line._amosTrackConfig || {};
      var state = spacePassState(track, elapsed);
      var active = state.phase === "active";
      line.setStyle({
        color: track.color || "#9bacc2",
        weight: active ? Number(track.active_weight || 1.8) : Number(track.weight || 1.0),
        opacity: active ? Number(track.active_opacity || 0.48) : Number(track.opacity == null ? 0.16 : track.opacity),
        dashArray: active ? (track.active_dash_array || "5 7") : (track.dash_array || "3 10"),
      });
      line.setTooltipContent(escapeHtml(track.label || "卫星预测星下轨迹") + " · " + escapeHtml(state.label));
    });
  }

  function position(item) {
    var pos = item.position || item;
    return {lat: Number(pos.lat), lng: Number(pos.lng == null ? pos.lon : pos.lng)};
  }

  function ownLabel(asset) {
    var role = asset.role || asset.type || asset.id || "己方平台";
    var memberCount = Number(asset.swarm_size || asset.member_count || 0);
    var formation = memberCount > 1 ? " · " + memberCount + (asset.domain === "maritime" ? "舰" : "机") : "";
    var status = String(asset.status || "").toLowerCase();
    if (status === "staged") return role + formation + " · 待命";
    if (status === "holding") return role + formation + " · 保持";
    if (status === "unavailable") return role + formation + " · 不可用";
    if (status === "degraded") return role + formation + " · 降级";
    if (asset.domain === "space") return role + formation + " · 轨道过境";
    var speed = Number(asset.speed_kts || 0);
    if (speed <= 0) return role + formation + (asset.domain === "ground" ? " · 固定" : " · 静止");
    if (asset.domain === "ground") return role + formation + " · " + Math.round(speed * 1.852) + " km/h";
    return role + formation + " · " + Math.round(speed) + " kt";
  }

  function ownTrailStyle(asset) {
    var kind = ownKind(asset);
    if (kind === "satellite") return {color: "#b8a7cf", opacity: 0.20, weight: 1.1, dashArray: "4 8", points: 36, smooth: true};
    if (kind === "uavSwarm" || kind === "loiterUav") return {color: "#9bbdc7", opacity: 0.32, weight: 1.25, dashArray: "3 5", points: 48, smooth: true};
    if (asset.domain === "air") return {color: "#9fb9c1", opacity: 0.30, weight: 1.25, dashArray: "4 6", points: 54, smooth: true};
    return {color: "#8eabb3", opacity: 0.25, weight: 1.15, dashArray: "2 6", points: 60, smooth: false};
  }

  function visualAssetPosition(marker, asset, actual) {
    if (!marker || String(asset.domain || "") !== "space") return actual;
    var factor = Number(scenarioView && scenarioView.spaceVisualSpeedFactor);
    if (!Number.isFinite(factor)) factor = 1;
    factor = Math.max(0.01, Math.min(1, factor));
    marker._amosActualPosition = {lat: actual.lat, lng: actual.lng};
    if (factor >= 0.999) return actual;
    var displayed = marker.getLatLng();
    var deltaLat = actual.lat - displayed.lat;
    var deltaLng = actual.lng - displayed.lng;
    if (deltaLng > 180) deltaLng -= 360;
    if (deltaLng < -180) deltaLng += 360;
    return {
      lat: displayed.lat + deltaLat * factor,
      lng: displayed.lng + deltaLng * factor,
    };
  }

  function isPresentationAssetListed(key, asset) {
    if (!scenarioView || !Array.isArray(scenarioView[key])) return true;
    var id = String(asset.id || asset.asset_id || "");
    return scenarioView[key].indexOf(id) !== -1;
  }

  function syncOwnLabel(marker, asset) {
    if (!marker) return;
    if (!isPresentationAssetListed("labelAssetIds", asset)) {
      if (marker.getTooltip && marker.getTooltip()) marker.unbindTooltip();
      marker._amosTooltipContent = "";
      return;
    }
    var label = ownLabel(asset);
    if (!marker.getTooltip || !marker.getTooltip()) {
      bindLabel(marker, label, "own-label", "center");
    } else {
      updateMarkerLabel(marker, label);
    }
  }

  function ownIconSize(asset) {
    var kind = ownKind(asset);
    if (kind === "satellite") return 22;
    if (kind === "commandShip" || kind === "aircraftCarrier") return 30;
    if (kind === "j16" || kind === "wz10") return 27;
    if (kind === "uavSwarm" || kind === "loiterUav") return 25;
    return 26;
  }

  function trackIconSize(kind) {
    return /MissileSite|destroyed|impact/.test(String(kind)) ? 29 : 26;
  }

  function trackLabel(track) {
    var contact = window.PlatformPanels && window.PlatformPanels.contactLabel
      ? window.PlatformPanels.contactLabel(track)
      : (track.id || track.track_id || "海面接触");
    return contact + " · " + trackAssessmentLabel(track);
  }

  function bindLabel(marker, label, className, direction) {
    var labelDirection = direction || "bottom";
    var offset = labelDirection === "left" ? [-18, 0]
      : (labelDirection === "right" ? [18, 0] : (labelDirection === "center" ? [0, 0] : [0, 18]));
    marker.bindTooltip(label, {
      permanent: true, direction: labelDirection, offset: offset, opacity: 1,
      className: "map-resource-label " + (className || ""),
    });
    marker._amosTooltipContent = label;
  }

  function updateMarkerLabel(marker, label) {
    if (!marker || marker._amosTooltipContent === label) return;
    marker.setTooltipContent(label);
    marker._amosTooltipContent = label;
  }

  function rectanglesOverlap(first, second) {
    return first.left < second.right && first.right > second.left &&
      first.top < second.bottom && first.bottom > second.top;
  }

  function labelCandidates(marker, width, height) {
    var iconGap = 28;
    var horizontal = width / 2 + iconGap;
    var vertical = height / 2 + iconGap;
    var kind = marker._amosIconKind || "";
    var slot = Number(marker._amosLabelSlot || 0);
    var generic = [
      [horizontal, 0], [-horizontal, 0], [0, vertical], [0, -vertical],
      [horizontal, vertical], [-horizontal, -vertical], [horizontal, -vertical], [-horizontal, vertical],
    ];
    if (kind === "escort") {
      return [[horizontal, 0], [horizontal, -vertical], [horizontal, vertical]].concat(generic);
    }
    if (kind === "merchant") {
      return (slot % 2 === 0
        ? [[-horizontal, -vertical], [-horizontal, 0], [0, -vertical]]
        : [[-horizontal, vertical], [0, vertical], [-horizontal, 0]]).concat(generic);
    }
    if (kind === "shoreRadar") {
      return [[-horizontal, 0], [-horizontal, -vertical], [-horizontal, vertical]].concat(generic);
    }
    if (kind === "aew" || kind === "uav" || kind === "uavSwarm" || kind === "satellite") {
      return [[horizontal, -vertical], [horizontal, 0], [-horizontal, -vertical]].concat(generic);
    }
    return generic;
  }

  function labelPenalty(rect, occupied, iconRects, mapSize) {
    var penalty = 0;
    occupied.forEach(function (other) {
      if (rectanglesOverlap(rect, other)) penalty += 100000;
    });
    iconRects.forEach(function (iconRect) {
      if (rectanglesOverlap(rect, iconRect)) penalty += 120000;
    });
    if (rect.left < 5) penalty += (5 - rect.left) * 500;
    if (rect.top < 5) penalty += (5 - rect.top) * 500;
    if (rect.right > mapSize.x - 5) penalty += (rect.right - mapSize.x + 5) * 500;
    if (rect.bottom > mapSize.y - 5) penalty += (rect.bottom - mapSize.y + 5) * 500;
    return penalty;
  }

  function applyOwnLabelOffset(marker, offset) {
    var tooltip = marker.getTooltip && marker.getTooltip();
    if (!tooltip) return;
    tooltip.options.offset = L.point(offset[0], offset[1]);
    if (tooltip._updatePosition) tooltip._updatePosition();
    var element = tooltip.getElement && tooltip.getElement();
    if (!element) return;
    var length = Math.max(0, Math.hypot(offset[0], offset[1]) - 16);
    var angle = Math.atan2(-offset[1], -offset[0]) * 180 / Math.PI;
    element.style.setProperty("--label-leader-length", length.toFixed(1) + "px");
    element.style.setProperty("--label-leader-angle", angle.toFixed(1) + "deg");
    element.classList.toggle("label-low-zoom", map.getZoom() < 8);
    element.classList.add("label-decluttered");
  }

  function layoutOwnLabels() {
    ownLabelLayoutFrame = null;
    if (!map) return;
    var markers = Object.keys(ownMarkers).map(function (id) { return ownMarkers[id]; })
      .filter(function (marker) { return marker && map.hasLayer(marker) && marker.getTooltip(); });
    var mapSize = map.getSize();
    var iconRects = markers.map(function (marker) {
      var point = map.latLngToContainerPoint(marker.getLatLng());
      var radius = Math.max(12, Number(marker._amosIconSize || 26) / 2 + 4);
      return {left: point.x - radius, top: point.y - radius, right: point.x + radius, bottom: point.y + radius};
    });
    var occupied = [];
    markers.sort(function (first, second) {
      var priority = {escort: 0, shoreRadar: 1, aew: 2, uav: 3, merchant: 4};
      var firstPriority = priority[first._amosIconKind] == null ? 5 : priority[first._amosIconKind];
      var secondPriority = priority[second._amosIconKind] == null ? 5 : priority[second._amosIconKind];
      return firstPriority - secondPriority || first._amosLabelSlot - second._amosLabelSlot;
    });
    markers.forEach(function (marker) {
      var tooltip = marker.getTooltip();
      var element = tooltip.getElement && tooltip.getElement();
      if (!element) return;
      var markerPoint = map.latLngToContainerPoint(marker.getLatLng());
      var width = Math.max(60, element.offsetWidth || 0);
      var height = Math.max(18, element.offsetHeight || 0);
      var candidates = labelCandidates(marker, width, height);
      var best = null;
      candidates.forEach(function (offset, index) {
        var centerX = markerPoint.x + offset[0];
        var centerY = markerPoint.y + offset[1];
        var rect = {
          left: centerX - width / 2, top: centerY - height / 2,
          right: centerX + width / 2, bottom: centerY + height / 2,
        };
        var score = labelPenalty(rect, occupied, iconRects, mapSize) + index;
        if (!best || score < best.score) best = {offset: offset, rect: rect, score: score};
      });
      applyOwnLabelOffset(marker, best.offset);
      occupied.push(best.rect);
    });
  }

  function scheduleOwnLabelLayout() {
    if (ownLabelLayoutFrame) window.cancelAnimationFrame(ownLabelLayoutFrame);
    ownLabelLayoutFrame = window.requestAnimationFrame(layoutOwnLabels);
  }

  function updateMarkerPopup(marker, html) {
    if (!marker || marker._amosPopupContent === html) return;
    if (marker.getPopup && marker.getPopup()) marker.setPopupContent(html);
    else marker.bindPopup(html);
    marker._amosPopupContent = html;
  }

  function removeLayer(layer) {
    if (layer && layer._amosMotionFrame) {
      window.cancelAnimationFrame(layer._amosMotionFrame);
      layer._amosMotionFrame = null;
      layer._amosMotionTarget = null;
    }
    if (layer && map && map.hasLayer(layer)) map.removeLayer(layer);
  }

  function showWeaponImpact(weaponId, impactPosition) {
    if (!map || processedWeaponImpacts[weaponId]) return;
    processedWeaponImpacts[weaponId] = true;
    var layer = L.marker([impactPosition.lat, impactPosition.lng], {
      interactive: false,
      keyboard: false,
      zIndexOffset: 1200,
      icon: L.divIcon({
        className: "weapon-impact-marker",
        html: '<span class="weapon-impact-effect"><i class="impact-core"></i><i class="impact-ring impact-ring-one"></i><i class="impact-ring impact-ring-two"></i><i class="impact-sparks"></i></span>',
        iconSize: [54, 54],
        iconAnchor: [27, 27],
      }),
    }).addTo(map);
    weaponImpactLayers[weaponId] = layer;
    window.setTimeout(function () {
      removeLayer(layer);
      delete weaponImpactLayers[weaponId];
    }, 1700);
  }

  function updateDestroyedImpactMarkers(events, destroyedTrackIds) {
    var seen = {};
    (events || []).forEach(function (event) {
      if ((event.type !== "weapon_hit" && event.type !== "damage_assessment_confirmed") ||
          event.damage_state !== "destroyed") return;
      var trackId = String(event.target_track_id || "");
      if (trackId && destroyedTrackIds[trackId]) return;
      var impact = position(event.position || {});
      var markerId = String(event.weapon_id || trackId || "impact");
      if (!Number.isFinite(impact.lat) || !Number.isFinite(impact.lng)) return;
      seen[markerId] = true;
      if (!destroyedImpactMarkers[markerId]) {
        destroyedImpactMarkers[markerId] = L.marker([impact.lat, impact.lng], {
          interactive: true,
          icon: icon("destroyed", 0, 32),
        }).addTo(map);
        destroyedImpactMarkers[markerId]._amosIconKind = "destroyed";
        destroyedImpactMarkers[markerId]._amosIconSize = 32;
        bindLabel(destroyedImpactMarkers[markerId], "打击点 · 已击毁", "destroyed-label");
      } else {
        moveMarker(destroyedImpactMarkers[markerId], impact);
      }
      updateMarkerPopup(destroyedImpactMarkers[markerId],
        "<b>打击点</b><br>目标已击毁 · 威胁解除");
    });
    Object.keys(destroyedImpactMarkers).forEach(function (markerId) {
      if (!seen[markerId]) {
        removeLayer(destroyedImpactMarkers[markerId]);
        delete destroyedImpactMarkers[markerId];
      }
    });
  }

  function updateMarkerIcon(marker, kind, heading, size) {
    if (!marker) return;
    var actualSize = size || 34;
    if (marker._amosIconKind !== kind || marker._amosIconSize !== actualSize) {
      marker.setIcon(icon(kind, heading, actualSize));
      marker._amosIconKind = kind;
      marker._amosIconSize = actualSize;
      return;
    }
    var element = marker.getElement && marker.getElement();
    var symbolBody = element && element.querySelector(".symbol-body");
    if (symbolBody) {
      symbolBody.setAttribute("transform", "rotate(" + Number(heading || 0) + " 28 28)");
    }
  }

  function moveMarker(marker, targetPosition) {
    if (!marker || !Number.isFinite(targetPosition.lat) || !Number.isFinite(targetPosition.lng)) return;
    var start = marker.getLatLng();
    var target = L.latLng(targetPosition.lat, targetPosition.lng);
    var targetSignature = target.lat.toFixed(7) + ":" + target.lng.toFixed(7);
    if (marker._amosMotionTarget === targetSignature) return;
    if (!start || start.equals(target, 1e-9) || !window.requestAnimationFrame) {
      marker._amosMotionTarget = null;
      marker.setLatLng(target);
      return;
    }
    if (marker._amosMotionFrame) window.cancelAnimationFrame(marker._amosMotionFrame);
    marker._amosMotionTarget = targetSignature;
    var startedAt = window.performance.now();
    var updateCadence = marker._amosMotionUpdatedAt == null
      ? 450 : startedAt - marker._amosMotionUpdatedAt;
    marker._amosMotionUpdatedAt = startedAt;
    // Follow the actual stream cadence so a marker neither arrives early and
    // pauses nor spends several updates chasing an obsolete position.
    var durationMs = 450;
    if (updateCadence > 0 && updateCadence < 5000) {
      durationMs = Math.max(260, Math.min(900, updateCadence * 1.04));
    }
    function animate(now) {
      var progress = Math.min(1, Math.max(0, (now - startedAt) / durationMs));
      marker.setLatLng([
        start.lat + (target.lat - start.lat) * progress,
        start.lng + (target.lng - start.lng) * progress,
      ]);
      if (progress < 1 && map && map.hasLayer(marker)) {
        marker._amosMotionFrame = window.requestAnimationFrame(animate);
      } else {
        marker.setLatLng(target);
        marker._amosMotionFrame = null;
        marker._amosMotionTarget = null;
      }
    }
    marker._amosMotionFrame = window.requestAnimationFrame(animate);
  }

  function removeCollection(collection) {
    Object.keys(collection).forEach(function (key) { removeLayer(collection[key]); });
  }

  function createPane(name, zIndex) {
    var pane = map.createPane(name);
    pane.style.zIndex = String(zIndex);
    pane.style.pointerEvents = "none";
  }

  function addReliefLegend() {
    if (reliefLegend) return;
    reliefLegend = L.control({position: "bottomright"});
    reliefLegend.onAdd = function () {
      var div = L.DomUtil.create("div", "relief-legend");
      div.innerHTML = '<div class="relief-legend-title"><b>地形 / 水深</b><span>m</span></div>' +
        '<div class="relief-ramp land-ramp"><span>0</span><span>500</span><span>1500</span><span>3000+</span></div>' +
        '<div class="relief-ramp sea-ramp"><span>0</span><span>-200</span><span>-1000</span><span>-3000</span><span>-6000</span></div>' +
        '<small>ETOPO 2022 · 本地离线地形</small>';
      L.DomEvent.disableClickPropagation(div);
      return div;
    };
    reliefLegend.addTo(map);
  }

  function addCoordinateControl() {
    var control = L.control({position: "bottomleft"});
    control.onAdd = function () {
      var div = L.DomUtil.create("div", "map-coordinate-control");
      div.textContent = "鼠标坐标：—";
      map.on("mousemove", function (event) {
        var lat = event.latlng.lat;
        var lng = event.latlng.lng;
        div.textContent = "鼠标坐标：" + Math.abs(lat).toFixed(4) + "°" + (lat >= 0 ? "N" : "S") + "  " +
          Math.abs(lng).toFixed(4) + "°" + (lng >= 0 ? "E" : "W");
      });
      map.on("mouseout movestart", function () { div.textContent = "鼠标坐标：—"; });
      return div;
    };
    control.addTo(map);
  }

  function addNorthControl() {
    var control = L.control({position: "topleft"});
    control.onAdd = function () {
      var div = L.DomUtil.create("div", "map-north-control");
      div.innerHTML = "<b>N</b>";
      div.title = "真北";
      return div;
    };
    control.addTo(map);
  }

  function loadRelief(manifestPath) {
    var path = manifestPath || "/static/assets/maps/taiwan-se-relief/manifest.json";
    reliefManifestPath = path;
    return fetch(path).then(function (response) {
      if (!response.ok) throw new Error("offline relief manifest unavailable");
      return response.json();
    }).then(function (manifest) {
      reliefManifest = manifest;
      var bounds = manifest.bounds;
      var basePath = path.slice(0, path.lastIndexOf("/") + 1);
      var panes = {terrain: "terrainPane", hillshade: "hillshadePane", contours: "contourPane"};
      var classes = {terrain: "relief-terrain", hillshade: "relief-hillshade", contours: "relief-contours"};
      Object.keys(manifest.layers || {}).forEach(function (name) {
        var config = manifest.layers[name];
        var kind = config.kind || name;
        var layerBounds = config.bounds || bounds;
        var imageBounds = [[layerBounds.south, layerBounds.west], [layerBounds.north, layerBounds.east]];
        var cacheSuffix = manifest.cache_version ? "?v=" + encodeURIComponent(manifest.cache_version) : "";
        removeLayer(reliefLayers[name]);
        reliefLayers[name] = L.imageOverlay(basePath + config.path + cacheSuffix, imageBounds, {
          pane: panes[kind] || "terrainPane",
          className: classes[kind] || "",
          opacity: 0,
          interactive: false,
          crossOrigin: false,
        }).addTo(map);
        reliefLayers[name]._amosReliefKind = kind;
        reliefLayers[name]._amosMinZoom = Number(config.min_zoom == null ? -Infinity : config.min_zoom);
        reliefLayers[name]._amosMaxZoom = Number(config.max_zoom == null ? Infinity : config.max_zoom);
        reliefLayers[name]._amosBounds = L.latLngBounds(imageBounds);
      });
      addReliefLegend();
      applyLayerVisibility();
      return manifest;
    }).catch(function () {
      reliefManifest = null;
      reliefManifestPath = null;
      Object.keys(reliefLayers).forEach(function (name) { removeLayer(reliefLayers[name]); });
      reliefLayers = {};
      return null;
    });
  }

  function init() {
    map = L.map("map", {
      zoomControl: true,
      minZoom: 5,
      maxZoom: 14,
      preferCanvas: false,
      maxBounds: [[8, 105], [35, 135]],
      maxBoundsViscosity: 1,
    }).setView([23.50, 121.00], 8);
    map.on("zoomend", function () { applyLayerVisibility(); updateNaturalTerrain(); scheduleOwnLabelLayout(); });
    map.on("moveend", function () { applyLayerVisibility(); updateNaturalTerrain(); scheduleOwnLabelLayout(); });
    map.on("resize", scheduleOwnLabelLayout);
    createPane("terrainPane", 205);
    createPane("hillshadePane", 210);
    createPane("contourPane", 215);
    createPane("mapDetailPane", 220);
    createBaseMapLayer();
    L.control.scale({metric: true, imperial: true, maxWidth: 150, position: "bottomleft"}).addTo(map);
    addCoordinateControl();
    addNorthControl();
    setTimeout(function () { map.invalidateSize(); }, 0);
  }

  function renderTheaterAO(theater) {
    removeLayer(aoLayer);
    gridLayers.forEach(removeLayer);
    gridLayers = [];
    if (!theater || !theater.ao) return;
    var ao = theater.ao;
    aoLayer = L.rectangle([[ao.south, ao.west], [ao.north, ao.east]], {
      color: "#37b7ff", weight: 1.5, dashArray: "8 5",
      fillColor: scenarioSurface === "land" ? "#294434" : "#0a2434",
      fillOpacity: scenarioSurface === "land" ? 0.08 : 0.025,
      opacity: layerState.ao ? 0.7 : 0,
    }).addTo(map);
    var span = Math.max(ao.north - ao.south, ao.east - ao.west);
    var step = span <= 0.5 ? 0.05 : 0.25;
    for (var lat = Math.ceil(ao.south / step) * step; lat < ao.north; lat += step) {
      gridLayers.push(L.polyline([[lat, ao.west], [lat, ao.east]], {
        color: "#6f94a0", weight: 0.7, opacity: layerState.ao ? 0.16 : 0,
      }).addTo(map));
    }
    for (var lng = Math.ceil(ao.west / step) * step; lng < ao.east; lng += step) {
      gridLayers.push(L.polyline([[ao.south, lng], [ao.north, lng]], {
        color: "#6f94a0", weight: 0.7, opacity: layerState.ao ? 0.16 : 0,
      }).addTo(map));
    }
  }

  function clearSensors() {
    Object.keys(sensorLayers).forEach(function (id) {
      (sensorLayers[id] || []).forEach(removeLayer);
    });
    sensorLayers = {};
    sensorLayerSignatures = {};
    sensorPoseSignatures = {};
  }

  function sensorOperational(asset) {
    return ["active", "operational", "holding", "degraded"].indexOf(
      String(asset.status || "").toLowerCase()
    ) >= 0;
  }

  function sectorPoints(pos, heading, rangeNm, fovDeg) {
    var points = [[pos.lat, pos.lng]];
    var steps = Math.max(8, Math.ceil(Number(fovDeg) / 6));
    for (var index = 0; index <= steps; index += 1) {
      var bearing = Number(heading || 0) - Number(fovDeg) / 2 + Number(fovDeg) * index / steps;
      var angle = bearing * Math.PI / 180;
      var dLat = Number(rangeNm) / 60 * Math.cos(angle);
      var lonScale = Math.max(0.2, Math.cos(Number(pos.lat) * Math.PI / 180));
      var dLng = Number(rangeNm) / (60 * lonScale) * Math.sin(angle);
      points.push([Number(pos.lat) + dLat, Number(pos.lng) + dLng]);
    }
    points.push([pos.lat, pos.lng]);
    return points;
  }

  function sensorFootprints(asset, models) {
    if (!sensorOperational(asset)) return [];
    var pos = position(asset);
    var layers = [];
    (asset.sensors || []).forEach(function (name) {
      var key = String(name).replace(/ /g, "_").toUpperCase();
      var spec = models[key] || models[name];
      if (!spec || !Number(spec.range_nm)) return;
      var radius = Number(spec.range_nm) * 1852;
      var fov = Number(spec.fov_deg || 360);
      var directional = fov < 359.5;
      var style = {
        color: directional ? "#42d7ff" : "#52ff79",
        weight: 1, dashArray: "4 4", fillOpacity: layerState.sensors ? 0.025 : 0,
        opacity: layerState.sensors ? 0.5 : 0,
        // Moving/overlapping footprints must not compete for pointer hover.
        // Sensor details are available from the owning platform's stable popup.
        interactive: false,
      };
      var layer = directional
        ? L.polygon(sectorPoints(pos, asset.heading || asset.heading_deg, spec.range_nm, fov), style).addTo(map)
        : L.circle([pos.lat, pos.lng], Object.assign({radius: radius}, style)).addTo(map);
      layer._amosDirectional = directional;
      layer._amosRangeNm = Number(spec.range_nm);
      layer._amosFovDeg = fov;
      layers.push(layer);
    });
    return layers;
  }

  function renderAllSensorFootprints(assets, models) {
    clearSensors();
    sensorModels = models || {};
    (assets || []).forEach(function (asset) {
      var id = asset.asset_id || asset.id;
      sensorLayers[id] = sensorFootprints(asset, sensorModels);
      sensorLayerSignatures[id] = JSON.stringify(asset.sensors || []);
      var pos = position(asset);
      sensorPoseSignatures[id] = JSON.stringify([pos.lat, pos.lng, asset.heading || asset.heading_deg || 0]);
      // Sensor models arrive after the base scenario is drawn. Refresh the
      // platform popup now so range/FOV details are available before start.
      if (ownMarkers[id]) updateMarkerPopup(ownMarkers[id], assetPopupHtml(asset));
    });
  }

  function updateSensorFootprints(asset) {
    var id = asset.asset_id || asset.id;
    var signature = JSON.stringify(asset.sensors || []);
    var pos = position(asset);
    var poseSignature = JSON.stringify([pos.lat, pos.lng, asset.heading || asset.heading_deg || 0]);
    if (!sensorOperational(asset)) {
      (sensorLayers[id] || []).forEach(removeLayer);
      sensorLayers[id] = [];
      sensorLayerSignatures[id] = signature;
      sensorPoseSignatures[id] = poseSignature;
      return;
    }
    if (!sensorLayers[id] || !sensorLayers[id].length || sensorLayerSignatures[id] !== signature) {
      (sensorLayers[id] || []).forEach(removeLayer);
      sensorLayers[id] = sensorFootprints(asset, sensorModels);
      sensorLayerSignatures[id] = signature;
      sensorPoseSignatures[id] = poseSignature;
      return;
    }
    if (sensorPoseSignatures[id] === poseSignature) return;
    (sensorLayers[id] || []).forEach(function (layer) {
      if (layer._amosDirectional) {
        layer.setLatLngs(sectorPoints(
          pos, asset.heading || asset.heading_deg,
          layer._amosRangeNm, layer._amosFovDeg
        ));
      } else {
        layer.setLatLng([pos.lat, pos.lng]);
      }
    });
    sensorPoseSignatures[id] = poseSignature;
  }

  function clearAll() {
    if (ownLabelLayoutFrame) window.cancelAnimationFrame(ownLabelLayoutFrame);
    ownLabelLayoutFrame = null;
    removeCollection(ownMarkers);
    removeCollection(weaponMarkers);
    removeCollection(weaponImpactLayers);
    removeCollection(destroyedImpactMarkers);
    removeCollection(trackMarkers);
    removeCollection(ownTrails);
    removeCollection(trackTrails);
    removeCollection(coordinationLayers);
    protectedLayers.forEach(removeLayer);
    spaceGroundTrackLayers.forEach(removeLayer);
    clearSensors();
    ownMarkers = {};
    weaponMarkers = {};
    weaponImpactLayers = {};
    processedWeaponImpacts = {};
    destroyedImpactMarkers = {};
    trackMarkers = {};
    ownTrails = {};
    trackTrails = {};
    coordinationLayers = {};
    protectedLayers = [];
    spaceGroundTrackLayers = [];
  }

  function loadScenario(scenario) {
    clearAll();
    var theaterId = String(((scenario.theater || {}).theater_id) || "");
    var theaterRelief = theaterId === "taiwan_southeast_convoy_corridor"
      ? "/static/assets/maps/taiwan-se-relief/manifest.json" : null;
    var configuredRelief = (scenario.map_display || {}).relief_manifest || theaterRelief;
    // The new XYZ pack already contains shaded terrain and ocean relief.
    // Do not reload the previous ETOPO overlays when changing scenarios.
    layerState = Object.assign({
      terrain: Boolean(configuredRelief),
      hillshade: Boolean(configuredRelief),
      contours: Boolean(configuredRelief),
      sensors: false,
      coordination: true,
      ao: true,
    },
      (scenario.map_display || {}).default_layers || {});
    layerState.terrain = false;
    layerState.hillshade = false;
    layerState.contours = false;
    scenarioSurface = (scenario.map_display || {}).base_surface || "maritime";
    var theater = scenario.theater || {};
    var center = theater.center || {lat: 23.50, lng: 121.00};
    var ao = theater.ao || null;
    var mapDisplay = scenario.map_display || {};
    var excludedDomains = mapDisplay.exclude_domains_from_focus || [];
    var focusPoints = (scenario.assets || []).filter(function (asset) {
      return excludedDomains.indexOf(String(asset.domain || "")) === -1;
    }).map(function (asset) {
      var point = position(asset);
      return [point.lat, point.lng];
    }).filter(function (point) { return Number.isFinite(point[0]) && Number.isFinite(point[1]); });
    var operationalBounds = null;
    if (focusPoints.length > 1) {
      var padded = L.latLngBounds(focusPoints).pad(0.16);
      operationalBounds = [[padded.getSouth(), padded.getWest()], [padded.getNorth(), padded.getEast()]];
    }
    var configuredFocus = mapDisplay.focus_bounds;
    if (configuredFocus) {
      operationalBounds = [[configuredFocus.south, configuredFocus.west], [configuredFocus.north, configuredFocus.east]];
    }
    scenarioView = {
      center: [center.lat, center.lng],
      zoom: Number(theater.zoom || 12),
      excludeDomains: excludedDomains.slice(),
      fixedBounds: Boolean(configuredFocus),
      labelAssetIds: Array.isArray(mapDisplay.label_asset_ids) ? mapDisplay.label_asset_ids.slice() : null,
      trailAssetIds: Array.isArray(mapDisplay.trail_asset_ids) ? mapDisplay.trail_asset_ids.slice() : null,
      trailWindowSec: Math.max(60, Number(mapDisplay.trail_window_sec || 480)),
      trackTrailWindowSec: Math.max(60, Number(mapDisplay.track_trail_window_sec || 600)),
      spaceVisualSpeedFactor: Number(mapDisplay.space_visual_speed_factor == null ? 1 : mapDisplay.space_visual_speed_factor),
      spaceOperations: scenario.space_operations || null,
      spaceNodeAssetIds: Array.isArray(mapDisplay.space_node_asset_ids) ? mapDisplay.space_node_asset_ids.slice() : [],
      // The AO remains visible as a layer, while reset/focus frames the
      // currently deployed own-force envelope so movement is legible.
      bounds: operationalBounds || (ao ? [[ao.south, ao.west], [ao.north, ao.east]] : null),
    };
    renderTheaterAO(theater);
    renderSpaceGroundTracks(mapDisplay.space_ground_tracks || []);
    updateSpaceGroundTracks(0);
    renderSpaceOperations(0);
    renderProtectedAssets(
      scenario.protected_assets || [],
      Number(((scenario.engagement_policy || {}).protected_asset_buffer_nm) || 0) * 1852
    );
    (scenario.assets || []).forEach(function (asset, assetIndex) {
      // Spacecraft are time-windowed operational resources.  Their static
      // pre-run coordinates are not a live position and must not flash on map.
      if (String(asset.domain || "") === "space") return;
      var id = asset.asset_id || asset.id;
      var pos = position(asset);
      var assetIconSize = ownIconSize(asset);
      var marker = L.marker([pos.lat, pos.lng], {
        icon: icon(ownKind(asset), asset.heading || asset.heading_deg, assetIconSize),
      }).addTo(map);
      marker._amosIconKind = ownKind(asset);
      marker._amosIconSize = assetIconSize;
      marker._amosLabelSlot = assetIndex;
      marker._amosActualPosition = {lat: pos.lat, lng: pos.lng};
      syncOwnLabel(marker, asset);
      updateMarkerPopup(marker, assetPopupHtml(asset));
      ownMarkers[id] = marker;
    });
    applyLayerVisibility();
    focusScenarioView();
    scheduleOwnLabelLayout();
  }

  function smoothTrailPoints(points, passes) {
    var result = points.slice();
    for (var pass = 0; pass < (passes || 0) && result.length > 2; pass += 1) {
      var next = [result[0]];
      for (var index = 0; index < result.length - 1; index += 1) {
        var current = result[index];
        var following = result[index + 1];
        next.push([
          current[0] * 0.75 + following[0] * 0.25,
          current[1] * 0.75 + following[1] * 0.25,
        ]);
        next.push([
          current[0] * 0.25 + following[0] * 0.75,
          current[1] * 0.25 + following[1] * 0.75,
        ]);
      }
      next.push(result[result.length - 1]);
      result = next;
    }
    return result;
  }

  function trailDistanceNm(a, b) {
    var meanLat = (a[0] + b[0]) * Math.PI / 360;
    var northNm = (b[0] - a[0]) * 60;
    var eastNm = (b[1] - a[1]) * 60 * Math.cos(meanLat);
    return Math.sqrt(northNm * northNm + eastNm * eastNm);
  }

  function latestContinuousTrail(points, maximumJumpNm) {
    if (points.length < 2) return points;
    var segment = [points[0]];
    for (var index = 1; index < points.length; index += 1) {
      if (trailDistanceNm(points[index - 1], points[index]) > maximumJumpNm) {
        // Do not connect an association reset or a stale state refresh with a
        // map-spanning line. Show the current, continuous segment instead.
        segment = [points[index]];
      } else {
        segment.push(points[index]);
      }
    }
    return segment;
  }

  function renderTrail(store, id, rawPoints, style, maxPoints, smooth, windowSec) {
    var visualStyle = typeof style === "string" ? {color: style} : (style || {});
    var sourcePoints = (rawPoints || []).filter(function (item) {
      return item && item.lat != null && (item.lng != null || item.lon != null);
    });
    var latestTime = sourcePoints.reduce(function (result, item) {
      var value = Number(item.sim_time);
      return Number.isFinite(value) ? Math.max(result, value) : result;
    }, -Infinity);
    if (Number.isFinite(latestTime) && Number.isFinite(Number(windowSec))) {
      var cutoff = latestTime - Number(windowSec);
      sourcePoints = sourcePoints.filter(function (item) {
        return !Number.isFinite(Number(item.sim_time)) || Number(item.sim_time) >= cutoff;
      });
    }
    var points = sourcePoints.slice(-(maxPoints || 120)).map(function (item) {
      return [Number(item.lat), Number(item.lng == null ? item.lon : item.lng)];
    }).filter(function (item, index, rows) {
      if (!Number.isFinite(item[0]) || !Number.isFinite(item[1])) return false;
      return index === 0 || Math.abs(item[0] - rows[index - 1][0]) + Math.abs(item[1] - rows[index - 1][1]) > 1e-8;
    });
    points = latestContinuousTrail(points, smooth ? 12 : 6);
    if (smooth) points = smoothTrailPoints(points, 2);
    var lineStyle = {
      color: visualStyle.color || "#9fb9c1",
      weight: Number(visualStyle.weight || 1.2),
      opacity: Number(visualStyle.opacity == null ? 0.28 : visualStyle.opacity),
      dashArray: visualStyle.dashArray || null,
      lineCap: "round",
      lineJoin: "round",
    };
    var geometrySignature = JSON.stringify(lineStyle) + ":" + JSON.stringify(points);
    if (points.length > 1) {
      if (store[id]) {
        if (store[id]._amosGeometrySignature === geometrySignature) return;
        store[id].setLatLngs(points);
        store[id].setStyle(lineStyle);
      } else {
        store[id] = L.polyline(points, lineStyle).addTo(map);
        store[id].bindTooltip("历史航迹");
      }
      store[id]._amosGeometrySignature = geometrySignature;
    } else if (store[id]) {
      removeLayer(store[id]);
      delete store[id];
    }
  }

  function renderCoordinationLinks(links) {
    var styles = {
      intelligence: {color: "#b88cff", dashArray: "7 6", weight: 2.2},
      command: {color: "#ffe178", dashArray: "3 5", weight: 2.3},
      weapon: {color: "#ff695c", dashArray: "10 5", weight: 2.8},
    };
    var labels = {intelligence: "情报共享链", command: "指挥控制链", weapon: "协同武器链"};
    var seen = {};
    (links || []).forEach(function (link) {
      var id = String(link.link_id || "");
      var source = position(link.source_position || {});
      var target = position(link.target_position || {});
      if (!id || !Number.isFinite(source.lat) || !Number.isFinite(source.lng) ||
          !Number.isFinite(target.lat) || !Number.isFinite(target.lng)) return;
      seen[id] = true;
      var type = String(link.link_type || "coordination");
      var style = styles[type] || {color: "#78dce8", dashArray: "6 6", weight: 2};
      var opacity = layerState.coordination ? (link.status === "degraded" ? 0.35 : 0.78) : 0;
      var points = [[source.lat, source.lng], [target.lat, target.lng]];
      var signature = JSON.stringify(points) + ":" + type + ":" + link.status;
      if (!coordinationLayers[id]) {
        coordinationLayers[id] = L.polyline(points, {
          color: style.color,
          weight: style.weight,
          opacity: opacity,
          dashArray: style.dashArray,
          className: "coordination-link coordination-" + type,
          interactive: true,
        }).addTo(map);
        coordinationLayers[id].bindTooltip(
          escapeHtml(link.label || labels[type] || "协同链路") + " · " + escapeHtml(link.status || "active"),
          {sticky: true}
        );
      } else if (coordinationLayers[id]._amosSignature !== signature) {
        coordinationLayers[id].setLatLngs(points);
        coordinationLayers[id].setStyle({
          color: style.color, weight: style.weight, opacity: opacity, dashArray: style.dashArray,
        });
        coordinationLayers[id].setTooltipContent(
          escapeHtml(link.label || labels[type] || "协同链路") + " · " + escapeHtml(link.status || "active")
        );
      } else {
        coordinationLayers[id].setStyle({opacity: opacity});
      }
      coordinationLayers[id]._amosBaseOpacity = link.status === "degraded" ? 0.35 : 0.78;
      coordinationLayers[id]._amosSignature = signature;
    });
    Object.keys(coordinationLayers).forEach(function (id) {
      if (!seen[id]) {
        removeLayer(coordinationLayers[id]);
        delete coordinationLayers[id];
      }
    });
  }

  function updateLiveState(assets, weapons, tracks, elapsedSec, events, coordinationLinks) {
    updateSpaceGroundTracks(elapsedSec);
    renderSpaceOperations(elapsedSec);
    var liveExcludedDomains = scenarioView && scenarioView.excludeDomains || [];
    var liveFocusPoints = (assets || []).filter(sensorOperational).filter(function (asset) {
      return liveExcludedDomains.indexOf(String(asset.domain || "")) === -1;
    }).map(function (asset) {
      var point = position(asset);
      return [point.lat, point.lng];
    }).filter(function (point) { return Number.isFinite(point[0]) && Number.isFinite(point[1]); });
    if (scenarioView && !scenarioView.fixedBounds) {
      if (liveFocusPoints.length > 1) {
        var liveBounds = L.latLngBounds(liveFocusPoints).pad(0.16);
        scenarioView.liveBounds = [
          [liveBounds.getSouth(), liveBounds.getWest()],
          [liveBounds.getNorth(), liveBounds.getEast()]
        ];
      } else if (liveFocusPoints.length === 1) {
        scenarioView.liveCenter = liveFocusPoints[0];
        scenarioView.liveBounds = null;
      }
    }
    var seenAssets = {};
    (assets || []).forEach(function (asset, assetIndex) {
      var id = asset.id || asset.asset_id;
      if (scenarioView && scenarioView.spaceNodeAssetIds.indexOf(String(id)) !== -1) {
        if (ownMarkers[id]) {
          removeLayer(ownMarkers[id]);
          removeLayer(ownTrails[id]);
          delete ownMarkers[id];
          delete ownTrails[id];
        }
        return;
      }
      var pos = position(asset);
      seenAssets[id] = true;
      if (!ownMarkers[id]) {
        var assetIconSize = ownIconSize(asset);
        ownMarkers[id] = L.marker([pos.lat, pos.lng], {icon: icon(ownKind(asset), asset.heading, assetIconSize)}).addTo(map);
        ownMarkers[id]._amosIconKind = ownKind(asset);
        ownMarkers[id]._amosIconSize = assetIconSize;
        ownMarkers[id]._amosLabelSlot = assetIndex;
        ownMarkers[id]._amosActualPosition = {lat: pos.lat, lng: pos.lng};
        syncOwnLabel(ownMarkers[id], asset);
      } else {
        ownMarkers[id]._amosLabelSlot = assetIndex;
        moveMarker(ownMarkers[id], visualAssetPosition(ownMarkers[id], asset, pos));
        updateMarkerIcon(ownMarkers[id], ownKind(asset), asset.heading, ownIconSize(asset));
        syncOwnLabel(ownMarkers[id], asset);
      }
      updateMarkerPopup(ownMarkers[id], assetPopupHtml(asset));
      if (isPresentationAssetListed("trailAssetIds", asset)) {
        var trailStyle = ownTrailStyle(asset);
        renderTrail(
          ownTrails, id, asset.history_path, trailStyle, trailStyle.points,
          trailStyle.smooth, scenarioView && scenarioView.trailWindowSec
        );
      } else if (ownTrails[id]) {
        removeLayer(ownTrails[id]);
        delete ownTrails[id];
      }
      updateSensorFootprints(asset);
    });
    Object.keys(ownMarkers).forEach(function (id) {
      if (!seenAssets[id]) {
        removeLayer(ownMarkers[id]);
        removeLayer(ownTrails[id]);
        (sensorLayers[id] || []).forEach(removeLayer);
        delete ownMarkers[id];
        delete ownTrails[id];
        delete sensorLayers[id];
        delete sensorLayerSignatures[id];
        delete sensorPoseSignatures[id];
      }
    });
    scheduleOwnLabelLayout();

    var seenWeapons = {};
    (weapons || []).forEach(function (weapon) {
      var id = weapon.id || weapon.weapon_id;
      var pos = position(weapon);
      if (!id || !Number.isFinite(pos.lat) || !Number.isFinite(pos.lng)) return;
      var status = String(weapon.status || "").toLowerCase();
      if (status === "hit") {
        var impactAt = Number(weapon.impact_sim_time);
        var currentElapsed = Number(elapsedSec);
        if (Number.isFinite(impactAt) && Number.isFinite(currentElapsed) &&
            currentElapsed >= impactAt && currentElapsed - impactAt <= 90) {
          showWeaponImpact(id, pos);
        } else {
          processedWeaponImpacts[id] = true;
        }
        return;
      }
      if (status === "aborted") return;
      // A time-on-target member remains a plan until its scheduled release;
      // do not place a live weapon symbol at the carrier's position early.
      if (status === "scheduled") return;
      seenWeapons[id] = true;
      if (!weaponMarkers[id]) {
        weaponMarkers[id] = L.marker([pos.lat, pos.lng], {
          icon: icon("weapon", weapon.heading, 28),
        }).addTo(map);
        weaponMarkers[id]._amosIconKind = "weapon";
        weaponMarkers[id]._amosIconSize = 28;
        bindLabel(weaponMarkers[id], "武器 · " + (weapon.status || "飞行中"), "threat-label");
      } else {
        moveMarker(weaponMarkers[id], pos);
        updateMarkerIcon(weaponMarkers[id], "weapon", weapon.heading, 28);
        updateMarkerLabel(weaponMarkers[id], "武器 · " + (weapon.status || "飞行中"));
      }
      updateMarkerPopup(weaponMarkers[id], "<b>" + escapeHtml(weapon.weapon_type || id) +
        "</b><br>状态 " + escapeHtml(weapon.status || "unknown") +
        "<br>目标航迹 " + escapeHtml(weapon.target_track_id || "—") +
        "<br>预计剩余 " + escapeHtml(weapon.eta_sec == null ? "—" : weapon.eta_sec + " 秒"));
    });
    Object.keys(weaponMarkers).forEach(function (id) {
      if (!seenWeapons[id]) {
        removeLayer(weaponMarkers[id]);
        delete weaponMarkers[id];
      }
    });

    var seenTracks = {};
    var destroyedTrackIds = {};
    (tracks || []).forEach(function (track, trackIndex) {
      if (track.lat == null || (track.lng == null && track.lon == null)) return;
      var id = track.id || track.track_id;
      var pos = position(track);
      var kind = trackKind(track);
      var heading = Number(track.heading || track.heading_deg || 0);
      if (kind === "destroyed") destroyedTrackIds[id] = true;
      seenTracks[id] = true;
      if (!trackMarkers[id]) {
        var currentTrackIconSize = trackIconSize(kind);
        trackMarkers[id] = L.marker([pos.lat, pos.lng], {icon: icon(kind, heading, currentTrackIconSize)}).addTo(map);
        trackMarkers[id]._amosIconKind = kind;
        trackMarkers[id]._amosIconSize = currentTrackIconSize;
        trackMarkers[id]._amosTrackKind = kind;
        trackMarkers[id]._amosLabelDirection = trackIndex % 2 === 0 ? "right" : "left";
        bindLabel(
          trackMarkers[id], trackLabel(track),
          trackLabelClass(kind),
          trackMarkers[id]._amosLabelDirection
        );
      } else {
        moveMarker(trackMarkers[id], pos);
        if (trackMarkers[id]._amosTrackKind !== kind) {
          updateMarkerIcon(trackMarkers[id], kind, heading, trackIconSize(kind));
          trackMarkers[id].unbindTooltip();
          bindLabel(
            trackMarkers[id], trackLabel(track),
            trackLabelClass(kind),
            trackMarkers[id]._amosLabelDirection
          );
          trackMarkers[id]._amosTrackKind = kind;
        } else {
          updateMarkerIcon(trackMarkers[id], kind, heading, trackIconSize(kind));
          updateMarkerLabel(trackMarkers[id], trackLabel(track));
        }
      }
      updateMarkerPopup(trackMarkers[id], "<b>" + escapeHtml(trackLabel(track)) + "</b><br>航迹编号 " +
        escapeHtml(id) + "<br>融合置信度 " +
        escapeHtml(track.confidence == null ? "—" : track.confidence) + "<br>分析状态 " +
        escapeHtml(trackAssessmentLabel(track)));
      renderTrail(
        trackTrails, id, track.history_path, trackTrailStyle(kind), 48, true,
        scenarioView && scenarioView.trackTrailWindowSec
      );
    });
    Object.keys(trackMarkers).forEach(function (id) {
      if (!seenTracks[id]) {
        removeLayer(trackMarkers[id]); removeLayer(trackTrails[id]);
        delete trackMarkers[id]; delete trackTrails[id];
      }
    });
    updateDestroyedImpactMarkers(events, destroyedTrackIds);
    renderCoordinationLinks(coordinationLinks);
  }

  function focusScenarioView() {
    if (!map || !scenarioView) return;
    map.invalidateSize();
    var bounds = scenarioView.liveBounds || scenarioView.bounds;
    if (bounds) {
      map.fitBounds(bounds, {
        paddingTopLeft: [44, 44], paddingBottomRight: [44, 44],
        maxZoom: scenarioView.zoom, animate: false,
      });
    } else {
      map.setView(scenarioView.liveCenter || scenarioView.center, scenarioView.zoom, {animate: false, reset: true});
    }
  }

  function invalidateSize() {
    if (map) map.invalidateSize({pan: false});
  }

  function toggleLayer(name) {
    layerState[name] = !layerState[name];
    applyLayerVisibility(name);
    return layerState[name];
  }

  function applyLayerVisibility(name) {
    if (!name || name === "terrain" || name === "hillshade" || name === "contours") {
      var reliefOpacity = {terrain: 0, hillshade: 0, contours: 0};
      var zoom = map ? map.getZoom() : 0;
      // Fine raster contours compete with symbols at theater scale.
      reliefOpacity.contours *= Math.max(0, Math.min(1, (zoom - 8) / 3));
      var viewBounds = map ? map.getBounds() : null;
      var layersByKind = {};
      Object.keys(reliefLayers).forEach(function (layerName) {
        var layer = reliefLayers[layerName];
        var kind = layer._amosReliefKind || layerName;
        if (!layersByKind[kind]) layersByKind[kind] = [];
        layersByKind[kind].push(layer);
      });
      Object.keys(layersByKind).forEach(function (kind) {
        var candidates = layersByKind[kind].filter(function (layer) {
          return !viewBounds || !layer._amosBounds || layer._amosBounds.contains(viewBounds);
        });
        candidates.sort(function (left, right) {
          function zoomDistance(layer) {
            if (zoom < layer._amosMinZoom) return layer._amosMinZoom - zoom;
            if (zoom > layer._amosMaxZoom) return zoom - layer._amosMaxZoom;
            return 0;
          }
          var distanceDelta = zoomDistance(left) - zoomDistance(right);
          if (distanceDelta) return distanceDelta;
          return right._amosMinZoom - left._amosMinZoom;
        });
        var selected = candidates[0] || null;
        layersByKind[kind].forEach(function (layer) {
          layer.setOpacity(layerState[kind] && layer === selected ? reliefOpacity[kind] : 0);
        });
      });
      if (reliefLegend && reliefLegend.getContainer()) {
        reliefLegend.getContainer().style.display = layerState.terrain ? "block" : "none";
      }
    }
    if (!name || name === "ao") {
      if (aoLayer) aoLayer.setStyle({
        opacity: layerState.ao ? 0.7 : 0,
        fillOpacity: layerState.ao ? (scenarioSurface === "land" ? 0.08 : 0.025) : 0,
      });
      gridLayers.forEach(function (layer) { layer.setStyle({opacity: layerState.ao ? 0.16 : 0}); });
    }
    if (!name || name === "sensors") {
      Object.keys(sensorLayers).forEach(function (id) {
        sensorLayers[id].forEach(function (layer) {
          layer.setStyle({opacity: layerState.sensors ? 0.5 : 0, fillOpacity: layerState.sensors ? 0.025 : 0});
        });
      });
    }
    if (!name || name === "coordination") {
      Object.keys(coordinationLayers).forEach(function (id) {
        coordinationLayers[id].setStyle({
          opacity: layerState.coordination ? coordinationLayers[id]._amosBaseOpacity || 0.78 : 0,
        });
      });
    }
  }

  return {
    init: init,
    loadScenario: loadScenario,
    updateLiveState: updateLiveState,
    renderTheaterAO: renderTheaterAO,
    renderAllSensorFootprints: renderAllSensorFootprints,
    clearSensorFootprints: clearSensors,
    focusScenarioView: focusScenarioView,
    invalidateSize: invalidateSize,
    toggleLayer: toggleLayer,
    toggleMapLabels: toggleMapLabels,
    getMapLabelsVisible: function () { return mapLabelsVisible; },
    getLayerState: function () { return Object.assign({}, layerState); },
    applyLayerVisibility: applyLayerVisibility,
    loadRelief: loadRelief,
  };
})();
