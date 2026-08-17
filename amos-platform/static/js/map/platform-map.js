/* Causal tactical map: current positions plus already-observed history only. */

window.PlatformMap = (function () {
  var map = null;
  var ownMarkers = {};
  var weaponMarkers = {};
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
  var layerState = {sensors: false, ao: true};

  var ICONS = {
    ship: '<svg viewBox="0 0 40 40"><path d="M20 4L29 30L20 35L11 30Z" fill="#0b2730" stroke="#42d7ff" stroke-width="2.4"/><path d="M20 8V29M14 25H26" stroke="#dffaff" stroke-width="2"/></svg>',
    air: '<svg viewBox="0 0 40 40"><path d="M20 4L24 17L35 23L34 28L23 24L23 34L28 37H12L17 34L17 24L6 28L5 23L16 17Z" fill="#12351d" stroke="#52ff79" stroke-width="2"/></svg>',
    uav: '<svg viewBox="0 0 40 40"><path d="M20 5L24 17L35 21L34 26L23 24L22 34H18L17 24L6 26L5 21L16 17Z" fill="#0a2e38" stroke="#42d7ff" stroke-width="2.2"/></svg>',
    facility: '<svg viewBox="0 0 40 40"><rect x="8" y="8" width="24" height="24" rx="3" fill="#092f25" stroke="#52ff79" stroke-width="2.2"/><circle cx="20" cy="20" r="3" fill="#dfffe6"/><path d="M20 17V10M13 15Q20 8 27 15" fill="none" stroke="#52ff79" stroke-width="1.8"/></svg>',
    ground: '<svg viewBox="0 0 40 40"><path d="M7 13h23l4 9v8H7Z" fill="#12351d" stroke="#52ff79" stroke-width="2.2"/><path d="M12 13l4-6h10l4 6" fill="none" stroke="#dfffe6" stroke-width="2"/><circle cx="13" cy="31" r="4" fill="#07131b" stroke="#52ff79" stroke-width="2"/><circle cx="29" cy="31" r="4" fill="#07131b" stroke="#52ff79" stroke-width="2"/></svg>',
    unknown: '<svg viewBox="0 0 40 40"><path d="M20 4L36 20L20 36L4 20Z" fill="#3c2b0c" stroke="#ffbf47" stroke-width="2.6"/><text x="20" y="26" text-anchor="middle" font-size="17" font-weight="700" fill="#fff1c9">?</text></svg>',
    threat: '<svg viewBox="0 0 40 40"><path d="M20 4L36 20L20 36L4 20Z" fill="#3b1010" stroke="#ff5544" stroke-width="2.6"/><text x="20" y="26" text-anchor="middle" font-size="15" font-weight="700" fill="#ffd8d3">!</text></svg>',
    weapon: '<svg viewBox="0 0 40 40"><path d="M20 3L26 25L20 37L14 25Z" fill="#451515" stroke="#ff705f" stroke-width="2.2"/><path d="M14 25L7 31L15 30M26 25L33 31L25 30" fill="none" stroke="#ffd0c8" stroke-width="2"/></svg>',
  };

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function icon(kind, heading, size) {
    var actualSize = size || 34;
    return L.divIcon({
      className: "rotating-marker",
      html: '<div class="marker-rotator" style="transform:rotate(' + Number(heading || 0) + 'deg);width:' + actualSize + 'px;height:' + actualSize + 'px">' + (ICONS[kind] || ICONS.unknown) + "</div>",
      iconSize: [actualSize, actualSize],
      iconAnchor: [actualSize / 2, actualSize / 2],
    });
  }

  function ownKind(asset) {
    var role = String(asset.role || asset.type || "");
    if (/设施|传感器站|指挥|雷达|节点|火力支援/.test(role)) return "facility";
    if (asset.domain === "ground") return "ground";
    if (asset.domain === "maritime") return "ship";
    if (/无人|UAV/.test(role)) return "uav";
    return "air";
  }

  function trackKind(track) {
    var assessment = track.agent_assessment || {};
    return assessment.status === "confirmed" && /high|hostile|threat/i.test(String(assessment.level || assessment.label || ""))
      ? "threat" : "unknown";
  }

  function trackAssessmentLabel(track) {
    var assessment = track.agent_assessment || {};
    var classification = String(track.classification || "").toUpperCase();
    if (assessment.source && /FISHING|CIVILIAN|MERCHANT/.test(classification)) return "民用禁射";
    return assessment.source ? (assessment.label || "已评估") : "未分类";
  }

  function position(item) {
    var pos = item.position || item;
    return {lat: Number(pos.lat), lng: Number(pos.lng == null ? pos.lon : pos.lng)};
  }

  function ownLabel(asset) {
    var role = asset.role || asset.type || asset.id || "己方平台";
    var status = String(asset.status || "").toLowerCase();
    if (status === "staged") return role + " · 待命";
    if (status === "holding") return role + " · 保持";
    if (status === "unavailable") return role + " · 不可用";
    if (status === "degraded") return role + " · 降级";
    var speed = Number(asset.speed_kts || 0);
    if (speed <= 0) return role + (asset.domain === "ground" ? " · 固定" : " · 静止");
    if (asset.domain === "ground") return role + " · " + Math.round(speed * 1.852) + " km/h";
    return role + " · " + Math.round(speed) + " kt";
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
    var rotator = element && element.querySelector(".marker-rotator");
    if (rotator) rotator.style.transform = "rotate(" + Number(heading || 0) + "deg)";
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
    var durationMs = 450;
    function animate(now) {
      var progress = Math.min(1, Math.max(0, (now - startedAt) / durationMs));
      marker.setLatLng([
        start.lat + (target.lat - start.lat) * progress,
        start.lng + (target.lng - start.lng) * progress,
      ]);
      if (progress < 1 && map && map.hasLayer(marker)) {
        marker._amosMotionFrame = window.requestAnimationFrame(animate);
      } else {
        marker._amosMotionFrame = null;
        marker._amosMotionTarget = null;
      }
    }
    marker._amosMotionFrame = window.requestAnimationFrame(animate);
  }

  function removeCollection(collection) {
    Object.keys(collection).forEach(function (key) { removeLayer(collection[key]); });
  }

  function init() {
    map = L.map("map", {
      zoomControl: true,
      minZoom: 5,
      maxZoom: 14,
      preferCanvas: false,
    }).setView([23.50, 121.00], 8);
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
      };
      var layer = directional
        ? L.polygon(sectorPoints(pos, asset.heading || asset.heading_deg, spec.range_nm, fov), style).addTo(map)
        : L.circle([pos.lat, pos.lng], Object.assign({radius: radius}, style)).addTo(map);
      layer._amosDirectional = directional;
      layer._amosRangeNm = Number(spec.range_nm);
      layer._amosFovDeg = fov;
      layer.bindTooltip(escapeHtml(name) + " · " + spec.range_nm + " NM · " + fov + "°");
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
    removeCollection(trackMarkers);
    removeCollection(ownTrails);
    removeCollection(trackTrails);
    clearSensors();
    ownMarkers = {};
    weaponMarkers = {};
    trackMarkers = {};
    ownTrails = {};
    trackTrails = {};
  }

  function loadScenario(scenario) {
    clearAll();
    layerState = Object.assign({sensors: false, ao: true},
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
      updateMarkerPopup(marker, "<b>" + escapeHtml(id) + "</b><br>" + escapeHtml(asset.role || "") +
        "<br>航速 " + escapeHtml(asset.speed_kts || 0) + " kt" +
        (asset.domain === "air" ? "<br>高度 " + escapeHtml(Math.round(Number((asset.position || {}).alt_ft || 0))) + " ft" : ""));
      ownMarkers[id] = marker;
    });
    focusScenarioView();
  }

  function renderTrail(store, id, rawPoints, color, maxPoints) {
    var points = (rawPoints || []).filter(function (item) {
      return item && item.lat != null && (item.lng != null || item.lon != null);
    }).slice(-(maxPoints || 120)).map(function (item) {
      return [Number(item.lat), Number(item.lng == null ? item.lon : item.lng)];
    });
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

  function updateLiveState(assets, weapons, tracks) {
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
      updateMarkerPopup(ownMarkers[id], "<b>" + escapeHtml(id) + "</b><br>" + escapeHtml(asset.role || "") +
        "<br>航向 " + escapeHtml(Math.round(Number(asset.heading || 0))) + "° · " +
        escapeHtml(Math.round(Number(asset.speed_kts || 0))) + " kt" +
        (asset.domain === "air" ? "<br>高度 " + escapeHtml(Math.round(Number((asset.position || {}).alt_ft || 0))) + " ft" : ""));
      renderTrail(ownTrails, id, asset.history_path, "#42d7ff", asset.domain === "air" ? 90 : 180);
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
    (tracks || []).forEach(function (track, trackIndex) {
      if (track.lat == null || (track.lng == null && track.lon == null)) return;
      var id = track.id || track.track_id;
      var pos = position(track);
      var kind = trackKind(track);
      seenTracks[id] = true;
      if (!trackMarkers[id]) {
        trackMarkers[id] = L.marker([pos.lat, pos.lng], {icon: icon(kind, 0, 34)}).addTo(map);
        trackMarkers[id]._amosIconKind = kind;
        trackMarkers[id]._amosIconSize = 34;
        trackMarkers[id]._amosTrackKind = kind;
        trackMarkers[id]._amosLabelDirection = trackIndex % 2 === 0 ? "right" : "left";
        bindLabel(
          trackMarkers[id], trackLabel(track),
          kind === "threat" ? "threat-label" : "unknown-label",
          trackMarkers[id]._amosLabelDirection
        );
      } else {
        moveMarker(trackMarkers[id], pos);
        if (trackMarkers[id]._amosTrackKind !== kind) {
          updateMarkerIcon(trackMarkers[id], kind, 0, 34);
          trackMarkers[id].unbindTooltip();
          bindLabel(
            trackMarkers[id], trackLabel(track),
            kind === "threat" ? "threat-label" : "unknown-label",
            trackMarkers[id]._amosLabelDirection
          );
          trackMarkers[id]._amosTrackKind = kind;
        } else {
          updateMarkerLabel(trackMarkers[id], trackLabel(track));
        }
      }
      updateMarkerPopup(trackMarkers[id], "<b>" + escapeHtml(trackLabel(track)) + "</b><br>航迹编号 " +
        escapeHtml(id) + "<br>融合置信度 " +
        escapeHtml(track.confidence == null ? "—" : track.confidence) + "<br>分析状态 " +
        escapeHtml(trackAssessmentLabel(track)));
      renderTrail(trackTrails, id, track.history_path, kind === "threat" ? "#ff5544" : "#ffbf47", 120);
    });
    Object.keys(trackMarkers).forEach(function (id) {
      if (!seenTracks[id]) {
        removeLayer(trackMarkers[id]); removeLayer(trackTrails[id]);
        delete trackMarkers[id]; delete trackTrails[id];
      }
    });
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
  };
})();
