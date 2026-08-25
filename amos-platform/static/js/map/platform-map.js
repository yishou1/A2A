/* Causal tactical map: current positions plus already-observed history only. */

window.PlatformMap = (function () {
  var map = null;
  var ownMarkers = {};
  var weaponMarkers = {};
  var weaponImpactLayers = {};
  var processedWeaponImpacts = {};
  var destroyedImpactMarkers = {};
  var trackMarkers = {};
  var ownTrails = {};
  var trackTrails = {};
  var sensorLayers = {};
  var sensorLayerSignatures = {};
  var sensorPoseSignatures = {};
  var sensorModels = {};
  var aoLayer = null;
  var gridLayers = [];
  var scenarioView = null;
  var scenarioSurface = "maritime";
  var layerState = {
    terrain: true, hillshade: true, contours: true, sensors: false, ao: true,
  };
  var reliefLayers = {};
  var reliefManifest = null;
  var reliefManifestPath = null;
  var reliefLegend = null;
  var SymbolLibrary = window.TacticalSymbols;

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function icon(kind, heading, size) {
    var actualSize = size || 34;
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
    if (assessment.damage_state === "destroyed" || assessment.engagement_status === "destroyed") {
      return "destroyed";
    }
    if (assessment.damage_state === "impact_pending" || assessment.engagement_status === "pending_assessment") {
      return "impact";
    }
    if (assessment.source && /FISHING|CIVILIAN|MERCHANT/.test(classification)) return "civilianSurface";
    return assessment.status === "confirmed" && /high|hostile|threat/i.test(String(assessment.level || assessment.label || ""))
      ? "hostileSurface" : "unknownSurface";
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

  function trackTrailColor(kind) {
    if (kind === "destroyed") return "#87969d";
    if (kind === "impact") return "#ffb24a";
    var affiliation = SymbolLibrary.affiliation(kind);
    if (affiliation === "hostile") return "#ff5544";
    if (affiliation === "civilian") return "#55dfb5";
    return "#ffbf47";
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
    return "<b>" + escapeHtml(id) + "</b><br>" + escapeHtml(asset.role || "") +
      "<br>航向 " + escapeHtml(Math.round(Number(asset.heading || asset.heading_deg || 0))) + "° · " +
      escapeHtml(Math.round(Number(asset.speed_kts || 0))) + " kt" +
      (/^(air|space)$/.test(String(asset.domain || "")) ? "<br>高度 " +
        escapeHtml(Math.round(Number((asset.position || {}).alt_ft || asset.alt_ft || 0))) + " ft" : "") +
      (memberCount > 1 ? "<br>蜂群规模 " + escapeHtml(memberCount) + " 架" : "") +
      sensorCapabilityHtml(asset);
  }

  function position(item) {
    var pos = item.position || item;
    return {lat: Number(pos.lat), lng: Number(pos.lng == null ? pos.lon : pos.lng)};
  }

  function ownLabel(asset) {
    var role = asset.role || asset.type || asset.id || "己方平台";
    var memberCount = Number(asset.swarm_size || asset.member_count || 0);
    var formation = memberCount > 1 ? " · " + memberCount + "机" : "";
    var status = String(asset.status || "").toLowerCase();
    if (status === "staged") return role + formation + " · 待命";
    if (status === "holding") return role + formation + " · 保持";
    if (status === "unavailable") return role + formation + " · 不可用";
    if (status === "degraded") return role + formation + " · 降级";
    if (asset.domain === "space") return role + formation + " · 星下点";
    var speed = Number(asset.speed_kts || 0);
    if (speed <= 0) return role + formation + (asset.domain === "ground" ? " · 固定" : " · 静止");
    if (asset.domain === "ground") return role + formation + " · " + Math.round(speed * 1.852) + " km/h";
    return role + formation + " · " + Math.round(speed) + " kt";
  }

  function ownTrailStyle(asset) {
    var kind = ownKind(asset);
    if (kind === "satellite") return {color: "#b98cff", points: 360, smooth: true};
    if (kind === "uavSwarm") return {color: "#81e6ff", points: 180, smooth: true};
    return {color: "#42d7ff", points: asset.domain === "air" ? 90 : 180, smooth: asset.domain === "air"};
  }

  function trackLabel(track) {
    var contact = window.PlatformPanels && window.PlatformPanels.contactLabel
      ? window.PlatformPanels.contactLabel(track)
      : (track.id || track.track_id || "海面接触");
    return contact + " · " + trackAssessmentLabel(track);
  }

  function bindLabel(marker, label, className, direction) {
    var labelDirection = direction || "bottom";
    var offset = labelDirection === "left" ? [-18, 0] : labelDirection === "right" ? [18, 0] : [0, 18];
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
      div.textContent = "22.2800°N  121.3200°E";
      map.on("mousemove", function (event) {
        var lat = event.latlng.lat;
        var lng = event.latlng.lng;
        div.textContent = Math.abs(lat).toFixed(4) + "°" + (lat >= 0 ? "N" : "S") + "  " +
          Math.abs(lng).toFixed(4) + "°" + (lng >= 0 ? "E" : "W");
      });
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
    }).setView([23.50, 121.00], 8);
    map.on("zoomend", function () { applyLayerVisibility(); });
    createPane("terrainPane", 205);
    createPane("hillshadePane", 210);
    createPane("contourPane", 215);
    protomapsL.leafletLayer({
      url: "/static/tiles/taiwan-southeast-tactical.pmtiles",
      flavor: "dark",
      lang: "zh",
      minZoom: 5,
      maxNativeZoom: 12,
      maxZoom: 14,
      noWrap: true,
      attribution: 'Protomaps · © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map);
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
    removeCollection(ownMarkers);
    removeCollection(weaponMarkers);
    removeCollection(weaponImpactLayers);
    removeCollection(destroyedImpactMarkers);
    removeCollection(trackMarkers);
    removeCollection(ownTrails);
    removeCollection(trackTrails);
    clearSensors();
    ownMarkers = {};
    weaponMarkers = {};
    weaponImpactLayers = {};
    processedWeaponImpacts = {};
    destroyedImpactMarkers = {};
    trackMarkers = {};
    ownTrails = {};
    trackTrails = {};
  }

  function loadScenario(scenario) {
    clearAll();
    var theaterId = String(((scenario.theater || {}).theater_id) || "");
    var theaterRelief = theaterId === "taiwan_southeast_convoy_corridor"
      ? "/static/assets/maps/taiwan-se-relief/manifest.json" : null;
    var configuredRelief = (scenario.map_display || {}).relief_manifest || theaterRelief;
    if (configuredRelief && configuredRelief !== reliefManifestPath) loadRelief(configuredRelief);
    layerState = Object.assign({
      terrain: Boolean(configuredRelief),
      hillshade: Boolean(configuredRelief),
      contours: Boolean(configuredRelief),
      sensors: false,
      ao: true,
    },
      (scenario.map_display || {}).default_layers || {});
    scenarioSurface = (scenario.map_display || {}).base_surface || "maritime";
    var theater = scenario.theater || {};
    var center = theater.center || {lat: 23.50, lng: 121.00};
    var ao = theater.ao || null;
    var focusPoints = (scenario.assets || []).map(function (asset) {
      var point = position(asset);
      return [point.lat, point.lng];
    }).filter(function (point) { return Number.isFinite(point[0]) && Number.isFinite(point[1]); });
    var operationalBounds = null;
    if (focusPoints.length > 1) {
      var padded = L.latLngBounds(focusPoints).pad(0.16);
      operationalBounds = [[padded.getSouth(), padded.getWest()], [padded.getNorth(), padded.getEast()]];
    }
    scenarioView = {
      center: [center.lat, center.lng],
      zoom: Number(theater.zoom || 12),
      // The AO remains visible as a layer, while reset/focus frames the
      // currently deployed own-force envelope so movement is legible.
      bounds: operationalBounds || (ao ? [[ao.south, ao.west], [ao.north, ao.east]] : null),
    };
    renderTheaterAO(theater);
    (scenario.assets || []).forEach(function (asset) {
      var id = asset.asset_id || asset.id;
      var pos = position(asset);
      var marker = L.marker([pos.lat, pos.lng], {
        icon: icon(ownKind(asset), asset.heading || asset.heading_deg, 34),
      }).addTo(map);
      marker._amosIconKind = ownKind(asset);
      marker._amosIconSize = 34;
      bindLabel(marker, ownLabel(asset), "own-label");
      updateMarkerPopup(marker, assetPopupHtml(asset));
      ownMarkers[id] = marker;
    });
    applyLayerVisibility();
    focusScenarioView();
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

  function renderTrail(store, id, rawPoints, color, maxPoints, smooth) {
    var points = (rawPoints || []).filter(function (item) {
      return item && item.lat != null && (item.lng != null || item.lon != null);
    }).slice(-(maxPoints || 120)).map(function (item) {
      return [Number(item.lat), Number(item.lng == null ? item.lon : item.lng)];
    }).filter(function (item, index, rows) {
      if (!Number.isFinite(item[0]) || !Number.isFinite(item[1])) return false;
      return index === 0 || Math.abs(item[0] - rows[index - 1][0]) + Math.abs(item[1] - rows[index - 1][1]) > 1e-8;
    });
    points = latestContinuousTrail(points, smooth ? 12 : 6);
    if (smooth) points = smoothTrailPoints(points, 2);
    var geometrySignature = color + ":" + JSON.stringify(points);
    if (points.length > 1) {
      if (store[id]) {
        if (store[id]._amosGeometrySignature === geometrySignature) return;
        store[id].setLatLngs(points);
        store[id].setStyle({color: color});
      } else {
        store[id] = L.polyline(points, {color: color, weight: 2, opacity: 0.65}).addTo(map);
        store[id].bindTooltip("历史航迹");
      }
      store[id]._amosGeometrySignature = geometrySignature;
    } else if (store[id]) {
      removeLayer(store[id]);
      delete store[id];
    }
  }

  function updateLiveState(assets, weapons, tracks, elapsedSec, events) {
    var liveFocusPoints = (assets || []).filter(sensorOperational).map(function (asset) {
      var point = position(asset);
      return [point.lat, point.lng];
    }).filter(function (point) { return Number.isFinite(point[0]) && Number.isFinite(point[1]); });
    if (scenarioView) {
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
    (assets || []).forEach(function (asset) {
      var id = asset.id || asset.asset_id;
      var pos = position(asset);
      seenAssets[id] = true;
      if (!ownMarkers[id]) {
        ownMarkers[id] = L.marker([pos.lat, pos.lng], {icon: icon(ownKind(asset), asset.heading, 34)}).addTo(map);
        ownMarkers[id]._amosIconKind = ownKind(asset);
        ownMarkers[id]._amosIconSize = 34;
        bindLabel(ownMarkers[id], ownLabel(asset), "own-label");
      } else {
        moveMarker(ownMarkers[id], pos);
        updateMarkerIcon(ownMarkers[id], ownKind(asset), asset.heading, 34);
        updateMarkerLabel(ownMarkers[id], ownLabel(asset));
      }
      updateMarkerPopup(ownMarkers[id], assetPopupHtml(asset));
      var trailStyle = ownTrailStyle(asset);
      renderTrail(ownTrails, id, asset.history_path, trailStyle.color, trailStyle.points, trailStyle.smooth);
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
        trackMarkers[id] = L.marker([pos.lat, pos.lng], {icon: icon(kind, heading, 34)}).addTo(map);
        trackMarkers[id]._amosIconKind = kind;
        trackMarkers[id]._amosIconSize = 34;
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
          updateMarkerIcon(trackMarkers[id], kind, heading, 34);
          trackMarkers[id].unbindTooltip();
          bindLabel(
            trackMarkers[id], trackLabel(track),
            trackLabelClass(kind),
            trackMarkers[id]._amosLabelDirection
          );
          trackMarkers[id]._amosTrackKind = kind;
        } else {
          updateMarkerIcon(trackMarkers[id], kind, heading, 34);
          updateMarkerLabel(trackMarkers[id], trackLabel(track));
        }
      }
      updateMarkerPopup(trackMarkers[id], "<b>" + escapeHtml(trackLabel(track)) + "</b><br>航迹编号 " +
        escapeHtml(id) + "<br>融合置信度 " +
        escapeHtml(track.confidence == null ? "—" : track.confidence) + "<br>分析状态 " +
        escapeHtml(trackAssessmentLabel(track)));
      renderTrail(trackTrails, id, track.history_path, trackTrailColor(kind), 120);
    });
    Object.keys(trackMarkers).forEach(function (id) {
      if (!seenTracks[id]) {
        removeLayer(trackMarkers[id]); removeLayer(trackTrails[id]);
        delete trackMarkers[id]; delete trackTrails[id];
      }
    });
    updateDestroyedImpactMarkers(events, destroyedTrackIds);
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
      var reliefOpacity = {terrain: 0.48, hillshade: 0.58, contours: 0.74};
      var zoom = map ? map.getZoom() : 0;
      Object.keys(reliefLayers).forEach(function (layerName) {
        var layer = reliefLayers[layerName];
        var kind = layer._amosReliefKind || layerName;
        var inZoomRange = zoom >= layer._amosMinZoom && zoom <= layer._amosMaxZoom;
        layer.setOpacity(layerState[kind] && inZoomRange ? reliefOpacity[kind] : 0);
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
    getLayerState: function () { return Object.assign({}, layerState); },
    applyLayerVisibility: applyLayerVisibility,
    loadRelief: loadRelief,
  };
})();
