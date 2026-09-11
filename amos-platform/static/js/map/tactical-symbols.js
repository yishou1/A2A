/* Offline tactical symbology for the active maritime air-defence scenario. */

window.TacticalSymbols = (function () {
  var palettes = {
    friendly: {stroke: "#67ddff", fill: "#072630", detail: "#e4faff"},
    unknown: {stroke: "#ffd166", fill: "#312608", detail: "#fff2bd"},
    hostile: {stroke: "#ff6657", fill: "#35100d", detail: "#ffe3df"},
    civilian: {stroke: "#76dbaa", fill: "#0d2c20", detail: "#e0fff0"},
    impact: {stroke: "#ffb24a", fill: "#412d17", detail: "#ffe0a8"},
    destroyed: {stroke: "#9aa7ad", fill: "#242a2e", detail: "#ff8a71"},
  };

  function ownKind(asset) {
    var id = String(asset.asset_id || asset.id || "").toUpperCase();
    var role = String(asset.role || asset.type || "");
    if (/SATELLITE|SPACECRAFT|\bSAT[-_]/.test(id) || /卫星|天基/.test(role) || asset.domain === "space") return "satellite";
    if (/\bCV[-_]/.test(id) || /航母/.test(role)) return "aircraftCarrier";
    if (/J16|J-16/.test(id) || /歼[-—]?16/.test(role)) return "j16";
    if (/WZ10|WZ-10/.test(id) || /无侦[-—]?10/.test(role)) return "wz10";
    if (/LOITER[-_]?UAV/.test(id) || /巡飞攻击无人机|自杀式无人机/.test(role)) return "loiterUav";
    if (/UAV[-_]?TANKER/.test(id) || /无人加油.*中继机|无人加油机/.test(role)) return "tankerUav";
    if (/UAV[-_]?C2/.test(id) || /指挥中继无人机/.test(role)) return "commandUav";
    if (/UAV[-_]?STRIKE/.test(id) || /舰载攻击无人机/.test(role)) return "strikeUav";
    if (/SEA[-_]C2|COMMAND[-_]?SHIP/.test(id) || /海上.*指挥舰|指挥舰编队/.test(role)) return "commandShip";
    if (/\bC2[-_]|COMMAND/.test(id) || /指挥中心|指挥所/.test(role)) return "commandCenter";
    if (/SWARM/.test(id) || /蜂群|集群无人机/.test(role)) return "uavSwarm";
    if (/SHORE-RADAR/.test(id) || /岸基.*雷达|警戒雷达/.test(role)) return "shoreRadar";
    if (/MERCHANT/.test(id) || /运输船|商船/.test(role)) return "merchant";
    if (/ESCORT/.test(id) || /护航舰|驱逐舰|护卫舰/.test(role)) return "escort";
    if (/AEW/.test(id) || /预警机/.test(role)) return "aew";
    if (/UAV/.test(id) || /无人机/.test(role)) return "uav";
    if (asset.domain === "maritime") return "escort";
    if (asset.domain === "ground") return "ground";
    return "aircraft";
  }

  function isHostile(track) {
    var assessment = track.agent_assessment || {};
    return assessment.status === "confirmed" &&
      /high|hostile|threat|威胁|敌/i.test(String(assessment.level || assessment.label || ""));
  }

  function trackKind(track) {
    var assessment = track.agent_assessment || {};
    var domain = String(track.domain_hint || track.domain || "").toLowerCase();
    var civilian = /civil|merchant|民用|商船/i.test(String(assessment.label || assessment.category || "")) ||
      track.ais_match === true;
    var prefix = civilian ? "civilian" : (isHostile(track) ? "hostile" : "unknown");
    if (/coastal_missile_site|missile_site|missile_battery/i.test(String(track.classification || ""))) {
      return prefix + "MissileSite";
    }
    if (/mobile_sam|sam_launcher|air_defen[cs]e_launcher/i.test(String(track.classification || ""))) return prefix + "MobileSam";
    if (/mobile_radar|radar_vehicle|early_warning_radar/i.test(String(track.classification || ""))) return prefix + "RadarVehicle";
    if (/runway|airfield_operating_surface/i.test(String(track.classification || ""))) return prefix + "Runway";
    if (/command_(facility|vehicle)|hardened_command|mobile_c2/i.test(String(track.classification || ""))) return prefix + "GroundCommand";
    if (/ground|land/.test(domain)) return prefix + "Ground";
    return prefix + (/air|aviation/.test(domain) ? "Air" : "Surface");
  }

  function silhouette(kind, palette) {
    var s = palette.stroke;
    var f = palette.fill;
    var d = palette.detail;
    if (kind === "satellite") {
      return '<path d="M22 21h12v14H22Z" fill="' + f + '" stroke="' + s + '" stroke-width="2"/>' +
        '<path d="M4 18h16v20H4zm32 0h16v20H36zM8 22h8M8 28h8M8 34h8M40 22h8M40 28h8M40 34h8" fill="none" stroke="' + s + '" stroke-width="1.3"/>' +
        '<path d="M28 21V11m-7-2q7-7 14 0-2 8-7 8t-7-8Zm7 26v9" fill="none" stroke="' + d + '" stroke-width="1.5"/><circle cx="28" cy="28" r="2.4" fill="' + d + '"/>';
    }
    if (kind === "aircraftCarrier") {
      return '<path d="M28 3 42 39 37 49 28 54 19 49 14 39Z" fill="' + f + '" stroke="' + s + '" stroke-width="2.2"/>' +
        '<path d="M19 14h17l4 25H16Zm4 5h10v20H23zm12 5h4v8h-4M20 43h16" fill="none" stroke="' + d + '" stroke-width="1.4"/>' +
        '<path d="M24 10h8M28 10V5m-7 2q7-6 14 0" fill="none" stroke="' + d + '" stroke-width="1.3"/>';
    }
    if (kind === "commandUav") {
      return '<path d="M28 5 33 20 49 26 47 32 33 29 31 45 37 50H19l6-5-2-16-15 3-1-6 16-6Z" fill="' + f + '" stroke="' + s + '" stroke-width="2"/>' +
        '<path d="M18 23h20M28 5v40" stroke="' + d + '" stroke-width="1.3"/><circle cx="28" cy="18" r="7" fill="none" stroke="' + d + '" stroke-width="1.5"/><path d="M19 11q9-8 18 0" fill="none" stroke="' + d + '" stroke-width="1.3"/>';
    }
    if (kind === "strikeUav") {
      return '<path d="M28 4 34 20 50 27 47 34 34 30 32 45 39 50H17l7-5-2-15-14 4-2-7 16-7Z" fill="' + f + '" stroke="' + s + '" stroke-width="2"/>' +
        '<path d="M17 25h22M28 5v39" stroke="' + d + '" stroke-width="1.3"/><path d="M14 36h8v4h-8zm20 0h8v4h-8z" fill="' + d + '"/>';
    }
    if (kind === "loiterUav") {
      return '<path d="M28 5 32 17 47 24 44 31 32 28 30 43 36 48H20l6-5-2-15-12 3-3-7 15-7Z" fill="' + f + '" stroke="' + s + '" stroke-width="2"/>' +
        '<path d="M18 22h20M28 5v40" stroke="' + d + '" stroke-width="1.2"/><path d="m22 46 6 7 6-7" fill="none" stroke="' + d + '" stroke-width="1.6"/><circle cx="28" cy="26" r="2.3" fill="' + d + '"/>';
    }
    if (kind === "tankerUav") {
      return '<path d="M28 4 33 19 49 26 47 33 33 29 31 44 38 49H18l7-5-2-15-15 4-2-7 17-7Z" fill="' + f + '" stroke="' + s + '" stroke-width="2"/>' +
        '<path d="M17 24h22M28 5v39" stroke="' + d + '" stroke-width="1.3"/><path d="M28 35v12m0 0-5 5m5-5 5 5" fill="none" stroke="' + d + '" stroke-width="1.6"/><circle cx="28" cy="34" r="2.2" fill="' + d + '"/>';
    }
    if (kind === "uavSwarm") {
      return '<path d="M28 5 31 17 42 22 41 27 31 25 30 36H26l-1-11-10 2-1-5 11-5Z" fill="' + f + '" stroke="' + s + '" stroke-width="1.8"/>' +
        '<path d="M14 29 17 39 25 43 24 47 17 45 16 52h-4l-1-7-7 2-1-4 8-4Zm28 0 3 10 8 4-1 4-7-2-1 7h-4l-1-7-7 2-1-4 8-4Z" fill="' + f + '" stroke="' + s + '" stroke-width="1.5"/>' +
        '<path d="M28 11v18M14 34v13M42 34v13" stroke="' + d + '" stroke-width="1"/>';
    }
    if (kind === "j16") {
      return '<path d="M28 3 34 20 49 29 46 35 34 31 39 46 34 49 28 40 22 49 17 46 22 31 10 35 7 29 22 20Z" fill="' + f + '" stroke="' + s + '" stroke-width="2"/>' +
        '<path d="M28 5v36M18 27h20M22 47l6-7 6 7" fill="none" stroke="' + d + '" stroke-width="1.4"/><circle cx="24" cy="34" r="1.8" fill="' + d + '"/><circle cx="32" cy="34" r="1.8" fill="' + d + '"/>';
    }
    if (kind === "wz10") {
      return '<path d="M28 4 33 19 49 25 47 32 33 29 32 44 39 49H17l7-5-1-15-14 3-2-7 16-6Z" fill="' + f + '" stroke="' + s + '" stroke-width="2"/>' +
        '<path d="M13 22h30M28 5v40M20 39h16" fill="none" stroke="' + d + '" stroke-width="1.4"/><path d="M23 23h10l-2 7h-6Z" fill="none" stroke="' + d + '" stroke-width="1.2"/>';
    }
    if (kind === "commandCenter") {
      return '<path d="M9 23 28 9 47 23v26H9Z" fill="' + f + '" stroke="' + s + '" stroke-width="2.2"/>' +
        '<path d="M16 49V28h24v21M28 28V12m-9-5q9-8 18 0M23 12q5-5 10 0" fill="none" stroke="' + d + '" stroke-width="1.6"/><circle cx="28" cy="16" r="2.3" fill="' + d + '"/><path d="M20 35h5v5h-5zm11 0h5v5h-5z" fill="' + d + '"/>';
    }
    if (kind === "commandShip") {
      return '<path d="M28 4 39 38 34 49 28 53 22 49 17 38Z" fill="' + f + '" stroke="' + s + '" stroke-width="2.2"/>' +
        '<path d="M21 21h14l3 17H18Zm4-9h8v9h-8zM28 12V5m-9 6q9-8 18 0" fill="none" stroke="' + d + '" stroke-width="1.5"/>' +
        '<circle cx="28" cy="25" r="3" fill="none" stroke="' + d + '" stroke-width="1.4"/><path d="M17 38h22M24 31h8" stroke="' + d + '" stroke-width="1.3"/>';
    }
    if (kind === "weapon") {
      return '<path d="M28 3 35 36 28 53 21 36Z" fill="' + f + '" stroke="' + s + '" stroke-width="2.2"/>' +
        '<path d="M21 36 12 45l11-3m12-6 9 9-11-3M28 8v30" fill="none" stroke="' + d + '" stroke-width="1.6"/>';
    }
    if (kind === "impact") {
      return '<path d="M10 35h36l-7 12H17Z" fill="' + f + '" stroke="' + s + '" stroke-width="2"/>' +
        '<path d="M15 13l26 29M41 13 15 42" stroke="' + d + '" stroke-width="2.8"/><circle cx="28" cy="28" r="7" fill="none" stroke="' + s + '" stroke-width="1.7"/>';
    }
    if (kind === "destroyed") {
      return '<path d="M10 35h36l-8 12H18Z" fill="' + f + '" stroke="' + s + '" stroke-width="2"/>' +
        '<path d="M15 12l26 30M41 12 15 42" stroke="' + d + '" stroke-width="3.2"/><path d="M13 50q7 3 14 0t14 0" fill="none" stroke="' + s + '" stroke-width="1.5"/>';
    }
    if (kind === "merchant") {
      return '<path d="M28 3 38 39 34 48 28 53 22 48 18 39Z" fill="' + f + '" stroke="' + s + '" stroke-width="2.2"/>' +
        '<path d="M22 19h12v20H22zm2-9h8v9h-8z" fill="none" stroke="' + d + '" stroke-width="1.4"/>' +
        '<path d="M22 26h12M22 32h12M18 39h20" stroke="' + d + '" stroke-width="1.1"/>';
    }
    if (kind === "escort") {
      return '<path d="M28 3 38 38 33 49 28 53 23 49 18 38Z" fill="' + f + '" stroke="' + s + '" stroke-width="2.2"/>' +
        '<path d="M23 18h10l2 18H21zm2-7h6v7h-6z" fill="none" stroke="' + d + '" stroke-width="1.5"/>' +
        '<circle cx="28" cy="24" r="3" fill="none" stroke="' + d + '" stroke-width="1.4"/><path d="M28 21V12M18 38h20" stroke="' + d + '" stroke-width="1.3"/>';
    }
    if (kind === "surfaceContact") {
      return '<path d="M28 6 37 37 33 47 28 51 23 47 19 37Z" fill="' + f + '" stroke="' + s + '" stroke-width="2.4"/>' +
        '<path d="M24 21h8l2 15H22zm-5 16h18M28 7v10" fill="none" stroke="' + d + '" stroke-width="1.5"/>';
    }
    if (kind === "fastCraft") {
      return '<path d="M28 5 40 39 35 47 28 52 21 47 16 39Z" fill="' + f + '" stroke="' + s + '" stroke-width="2.3"/>' +
        '<path d="M22 30 28 16 34 30 38 39H18Zm6-14V8" fill="none" stroke="' + d + '" stroke-width="1.5"/>';
    }
    if (kind === "aew") {
      return '<path d="M28 4 32 20 49 28 48 34 32 29 31 45 38 50H18l7-5-1-16-16 5-1-6 17-8Z" fill="' + f + '" stroke="' + s + '" stroke-width="2"/>' +
        '<ellipse cx="28" cy="17" rx="9" ry="3.5" fill="' + f + '" stroke="' + d + '" stroke-width="1.4"/><path d="M28 4v43" stroke="' + d + '" stroke-width="1.1"/>';
    }
    if (kind === "uav") {
      return '<path d="M28 5 32 20 48 26 47 32 32 29 31 45 35 49H21l4-4-1-16-15 3-1-6 16-6Z" fill="' + f + '" stroke="' + s + '" stroke-width="2"/>' +
        '<path d="M18 23h20M28 5v40" stroke="' + d + '" stroke-width="1.2"/><circle cx="28" cy="26" r="2.2" fill="' + d + '"/>';
    }
    if (kind === "shoreRadar") {
      return '<path d="M14 42h28v8H14z" fill="' + f + '" stroke="' + s + '" stroke-width="2"/>' +
        '<path d="M28 42V24M18 17q10-11 20 0-2 13-10 13-8 0-10-13Z" fill="' + f + '" stroke="' + s + '" stroke-width="2"/>' +
        '<path d="M28 24 39 12M14 38h28" stroke="' + d + '" stroke-width="1.5"/><circle cx="28" cy="24" r="2.5" fill="' + d + '"/>';
    }
    if (kind === "ground") {
      return '<rect x="10" y="14" width="36" height="28" rx="3" fill="' + f + '" stroke="' + s + '" stroke-width="2.2"/>' +
        '<path d="M15 37h26M20 14l5-7h12" stroke="' + d + '" stroke-width="1.5"/><circle cx="20" cy="44" r="4" fill="' + f + '" stroke="' + d + '"/><circle cx="38" cy="44" r="4" fill="' + f + '" stroke="' + d + '"/>';
    }
    if (kind === "missileSite") {
      return '<path d="M8 41h40v9H8Z" fill="' + f + '" stroke="' + s + '" stroke-width="2"/>' +
        '<path d="M15 40 36 15l6 5-18 20Z" fill="' + f + '" stroke="' + s + '" stroke-width="2"/>' +
        '<path d="m36 15 2-9 7 6-3 8M14 34l12 10M11 26q8-10 16-1" fill="none" stroke="' + d + '" stroke-width="1.5"/><circle cx="17" cy="50" r="3" fill="' + d + '"/><circle cx="39" cy="50" r="3" fill="' + d + '"/>';
    }
    if (kind === "mobileSam") {
      return '<rect x="8" y="35" width="40" height="11" rx="2" fill="' + f + '" stroke="' + s + '" stroke-width="2"/><circle cx="17" cy="48" r="4" fill="' + f + '" stroke="' + d + '"/><circle cx="39" cy="48" r="4" fill="' + f + '" stroke="' + d + '"/><path d="m17 34 18-22 7 5-15 18m7-23 2-7 7 6-1 6" fill="none" stroke="' + d + '" stroke-width="2"/>';
    }
    if (kind === "radarVehicle") {
      return '<rect x="8" y="35" width="40" height="11" rx="2" fill="' + f + '" stroke="' + s + '" stroke-width="2"/><circle cx="17" cy="48" r="4" fill="' + f + '" stroke="' + d + '"/><circle cx="39" cy="48" r="4" fill="' + f + '" stroke="' + d + '"/><path d="M28 35V23M17 15q11-12 22 0-3 12-11 12T17 15Zm11 8 12-12" fill="none" stroke="' + d + '" stroke-width="1.7"/>';
    }
    if (kind === "runway") {
      return '<path d="M21 5h14l5 46H16Z" fill="' + f + '" stroke="' + s + '" stroke-width="2"/><path d="M28 9v8m0 7v8m0 7v8M19 46h18M23 9h10" stroke="' + d + '" stroke-width="2"/>';
    }
    if (kind === "groundCommand") {
      return '<path d="M9 25 28 10 47 25v25H9Z" fill="' + f + '" stroke="' + s + '" stroke-width="2"/><path d="M17 50V30h22v20M28 30V14m-8-6q8-7 16 0M23 15q5-5 10 0" fill="none" stroke="' + d + '" stroke-width="1.5"/><circle cx="28" cy="19" r="2.3" fill="' + d + '"/>';
    }
    return '<path d="M28 4 33 21 50 28 49 34 33 30 31 46 38 51H18l7-5-2-16-16 4-1-6 17-7Z" fill="' + f + '" stroke="' + s + '" stroke-width="2"/>' +
      '<path d="M28 4v43" stroke="' + d + '" stroke-width="1.2"/>';
  }

  function affiliation(kind) {
    if (kind === "impact" || kind === "destroyed") return kind;
    if (kind === "weapon") return "friendly";
    if (/^hostile/.test(kind)) return "hostile";
    if (/^unknown/.test(kind)) return "unknown";
    if (/^civilian/.test(kind)) return "civilian";
    return "friendly";
  }

  function svg(kind) {
    var side = affiliation(kind);
    var palette = palettes[side];
    var frame = side === "hostile"
      ? '<path d="M28 1 55 28 28 55 1 28Z" fill="none" stroke="' + palette.stroke + '" stroke-width="1.7" opacity=".86"/>'
      : (side === "unknown"
        ? '<path d="M12 3h32l9 9v32l-9 9H12l-9-9V12Z" fill="none" stroke="' + palette.stroke + '" stroke-width="1.7" opacity=".86"/>'
        : '<rect x="2" y="2" width="52" height="52" rx="' + (side === "friendly" ? "7" : "1") + '" fill="none" stroke="' + palette.stroke + '" stroke-width="1.7" opacity=".86"/>');
    var baseKind = kind.replace(/^(unknown|hostile|civilian)/, "");
    if (baseKind === "Air") baseKind = "aircraft";
    if (baseKind === "Ground") baseKind = "ground";
    if (baseKind === "MissileSite") baseKind = "missileSite";
    if (baseKind === "MobileSam") baseKind = "mobileSam";
    if (baseKind === "RadarVehicle") baseKind = "radarVehicle";
    if (baseKind === "Runway") baseKind = "runway";
    if (baseKind === "GroundCommand") baseKind = "groundCommand";
    if (baseKind === "Surface") {
      baseKind = side === "civilian" ? "merchant" : (side === "hostile" ? "fastCraft" : "surfaceContact");
    }
    if (!/^(unknown|hostile|civilian)/.test(kind)) baseKind = kind;
    var heading = Number(arguments.length > 1 ? arguments[1] : 0) || 0;
    return '<svg viewBox="0 0 56 56" role="img" aria-hidden="true">' + frame +
      '<g class="symbol-body" transform="rotate(' + heading + ' 28 28)">' + silhouette(baseKind, palette) + '</g></svg>';
  }

  return {ownKind: ownKind, trackKind: trackKind, svg: svg, affiliation: affiliation};
})();
