"""Deterministic SVG products built only from frozen, public simulation state.

The renderer never receives scenario truth.  Callers provide one public capture
record plus an operator/Agent-visible snapshot.  Missing values remain visibly
unavailable instead of being replaced with decorative or simulated numbers.
"""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import math
import threading
from typing import Any
from urllib.parse import quote
from xml.sax.saxutils import escape


SVG_WIDTH = 960
SVG_HEIGHT = 540
UNAVAILABLE = "未提供"
SUPPORTED_RENDERERS = {
    "radar_ppi",
    "track_table",
    "ais_radar_correlation",
    "elint_spectrum",
    "network_topology",
    "link_monitor",
    "resource_status",
    "execution_state",
}


def _text(value: Any) -> str:
    if value is None or value == "":
        return UNAVAILABLE
    return str(value)


def _xml(value: Any) -> str:
    return escape(_text(value), {'"': "&quot;", "'": "&apos;"})


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _fmt(value: Any, digits: int = 1, suffix: str = "") -> str:
    number = _number(value)
    if number is None:
        return UNAVAILABLE
    return f"{number:.{digits}f}{suffix}"


def _list(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in (value or []) if isinstance(item, dict)]


def _position(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("position") if isinstance(record.get("position"), dict) else record
    return {
        "lat": value.get("lat"),
        "lng": value.get("lng", value.get("lon")),
        "alt_ft": value.get("alt_ft", value.get("altitude_ft")),
    }


def _source_parts(capture: dict[str, Any]) -> tuple[str | None, str | None]:
    sensor_id = capture.get("sensor_id")
    platform_id = capture.get("platform_id")
    sensor_instance_id = capture.get("sensor_instance_id")
    if sensor_id and "/" in str(sensor_id):
        source_platform, source_sensor = str(sensor_id).split("/", 1)
        platform_id = platform_id or source_platform
        sensor_instance_id = sensor_instance_id or source_sensor
    return (
        str(platform_id) if platform_id else None,
        str(sensor_instance_id or sensor_id) if (sensor_instance_id or sensor_id) else None,
    )


def resolve_renderer_type(capture: dict[str, Any]) -> str | None:
    """Resolve a visual product class without consulting any target reference."""
    parameters = capture.get("capture_parameters") if isinstance(capture.get("capture_parameters"), dict) else {}
    product_data = capture.get("product_data") if isinstance(capture.get("product_data"), dict) else {}
    explicit = (
        product_data.get("renderer_type")
        or product_data.get("visualization")
        or parameters.get("renderer_type")
        or parameters.get("visualization")
    )
    if explicit in SUPPORTED_RENDERERS:
        return str(explicit)

    searchable = " ".join(str(value or "") for value in (
        capture.get("modality"), capture.get("sensor_id"), capture.get("sensor_instance_id"),
        capture.get("capability_id"), capture.get("title"), capture.get("capture_id"),
    )).casefold()
    if any(token in searchable for token in ("elint", "comint", "spectrum", "频谱", "电子侦察", "rf")):
        return "elint_spectrum"
    if any(token in searchable for token in ("network", "link", "comms", "通信", "链路", "topology")):
        return "network_topology"
    if any(token in searchable for token in ("resource", "资源", "platform status", "availability")):
        return "resource_status"
    if any(token in searchable for token in ("execution", "执行", "command status")):
        return "execution_state"
    if any(token in searchable for token in ("fusion", "track", "航迹", "ais")):
        return "track_table"
    if any(token in searchable for token in ("radar", "雷达", "ppi", "aesa")):
        return "radar_ppi"
    return None


CAPTURE_KEYS = {
    "media_id", "capture_id", "product_type", "captured_at_sec", "captured_at_sim_time",
    "captured_at_tick_id", "at_sec",
    "platform_id", "sensor_instance_id", "capability_id", "sensor_id",
    "platform_pose", "sensor_pose", "observation_ids", "track_ids",
    "product_data", "capture_parameters", "sensor_config", "title", "caption", "modality",
    "mime_type", "media_type", "uri", "checksum",
    "dynamic_uri", "dynamic_checksum", "run_id",
    "consumer_context",
}


def build_public_capture_record(
    media: dict[str, Any],
    capture_plan: dict[str, Any] | None,
    frozen_state: dict[str, Any],
) -> dict[str, Any]:
    """Build a capture record by explicit whitelist; ``target_refs`` is ignored."""
    embedded = media.get("capture_record") if isinstance(media.get("capture_record"), dict) else {}
    merged: dict[str, Any] = {}
    for source in (capture_plan or {}, media, embedded):
        for key in CAPTURE_KEYS:
            if source.get(key) is not None:
                merged[key] = deepcopy(source[key])

    merged["media_id"] = media.get("media_id") or merged.get("media_id")
    merged["captured_at_sec"] = merged.get(
        "captured_at_sec",
        merged.get("captured_at_sim_time", merged.get("at_sec")),
    )
    platform_id, sensor_id = _source_parts(merged)
    if platform_id:
        merged["platform_id"] = platform_id
    if sensor_id:
        merged["sensor_instance_id"] = sensor_id

    assets = _list(frozen_state.get("assets") or frozen_state.get("own_asset_poses"))
    asset = next((item for item in assets if str(item.get("id") or item.get("asset_id")) == str(platform_id)), None)
    if asset and not isinstance(merged.get("platform_pose"), dict):
        merged["platform_pose"] = _position(asset)

    existing_product_data = merged.get("product_data") if isinstance(merged.get("product_data"), dict) else {}
    observations = _list(existing_product_data.get("observations")) or _list(frozen_state.get("observations"))
    requested_observation_ids = {
        str(value) for value in merged.get("observation_ids") or [] if value
    }
    matching_observations = []
    for observation in observations:
        if requested_observation_ids:
            if str(observation.get("observation_id") or "") in requested_observation_ids:
                matching_observations.append(observation)
            continue
        observation_platform = observation.get("asset_id") or observation.get("platform_id")
        observation_sensor = str(observation.get("sensor_id") or "")
        platform_match = platform_id and str(observation_platform) == str(platform_id)
        sensor_match = sensor_id and str(sensor_id).casefold() in observation_sensor.casefold()
        if platform_match or sensor_match:
            matching_observations.append(observation)
    if not merged.get("observation_ids"):
        merged["observation_ids"] = [
            str(item["observation_id"])
            for item in matching_observations
            if item.get("observation_id")
        ]

    track_ids = {str(value) for value in merged.get("track_ids") or [] if value}
    for observation in matching_observations:
        if observation.get("track_id"):
            track_ids.add(str(observation["track_id"]))
    merged["track_ids"] = sorted(track_ids)

    product_data = existing_product_data
    bearing_values = [
        value for item in matching_observations
        if (value := _number(item.get("bearing_deg"))) is not None
    ]
    range_values = [
        value for item in matching_observations
        if (value := _number(item.get("range_nm"))) is not None
    ]
    product_data = dict(product_data)
    product_data["observation_count"] = len(matching_observations)
    if bearing_values:
        product_data["bearing_summary_deg"] = (
            round(bearing_values[0], 1) if len(bearing_values) == 1
            else [round(min(bearing_values), 1), round(max(bearing_values), 1)]
        )
    if range_values:
        product_data["range_summary_nm"] = (
            round(range_values[0], 1) if len(range_values) == 1
            else [round(min(range_values), 1), round(max(range_values), 1)]
        )
    merged["product_data"] = product_data
    merged["renderer_type"] = resolve_renderer_type(merged)
    return merged


def _style() -> str:
    return """
      .bg{fill:#07131c}.panel{fill:#0b1a24;stroke:#294252}.line{stroke:#31505f;fill:none}
      .grid{stroke:#213946;fill:none}.accent{stroke:#53a9c9;fill:none}.ok{stroke:#55b58d;fill:none}
      .warn{stroke:#d2a24f;fill:none}.txt{fill:#cbd8df;font:14px 'DejaVu Sans',sans-serif}
      .small{fill:#80939f;font:12px 'DejaVu Sans',sans-serif}.tiny{fill:#687d89;font:10px 'DejaVu Sans Mono',monospace}
      .title{fill:#e3edf1;font:600 20px 'DejaVu Sans',sans-serif}.value{fill:#a9d9e9;font:600 14px 'DejaVu Sans Mono',monospace}
      .unknown{fill:#8999a2;font:13px 'DejaVu Sans',sans-serif}.dot{fill:#58adca;stroke:#b9e3f0;stroke-width:1}
    """


def _header(capture: dict[str, Any], snapshot: dict[str, Any], renderer_type: str) -> str:
    clock = snapshot.get("clock") if isinstance(snapshot.get("clock"), dict) else {}
    elapsed = clock.get("elapsed_sec")
    platform_id, sensor_id = _source_parts(capture)
    captured = capture.get("captured_at_sec", capture.get("at_sec"))
    consumer = capture.get("consumer_context") if isinstance(capture.get("consumer_context"), dict) else {}
    roles = "/".join(str(value) for value in consumer.get("functional_role_ids") or [])
    models = "/".join(str(value) for value in consumer.get("model_requirement_ids") or [])
    consumer_label = ""
    if roles or models:
        consumer_label = (
            f'<text x="928" y="42" text-anchor="end" class="tiny">'
            f'PLANNED INPUT · {_xml(roles or UNAVAILABLE)} · {_xml(models or UNAVAILABLE)} · TRACE REQUIRED</text>'
        )
    return f"""
      <text x="32" y="42" class="title">{_xml(capture.get('title') or renderer_type.replace('_', ' ').upper())}</text>
      {consumer_label}
      <text x="32" y="66" class="small">来源平台  {_xml(platform_id)}</text>
      <text x="300" y="66" class="small">传感器  {_xml(sensor_id)}</text>
      <text x="610" y="66" class="small">采集时刻  {_xml('T+' + _fmt(captured, 1, 's') if _number(captured) is not None else UNAVAILABLE)}</text>
      <text x="790" y="66" class="small">快照  {_xml('T+' + _fmt(elapsed, 1, 's') if _number(elapsed) is not None else UNAVAILABLE)}</text>
      <line x1="32" y1="82" x2="928" y2="82" class="line"/>
    """


def _observations_for_capture(capture: dict[str, Any], snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    product_data = capture.get("product_data") if isinstance(capture.get("product_data"), dict) else {}
    observations = _list(product_data.get("observations")) or _list(snapshot.get("observations"))
    ids = {str(value) for value in capture.get("observation_ids") or [] if value}
    if ids:
        return [item for item in observations if str(item.get("observation_id")) in ids]
    platform_id, sensor_id = _source_parts(capture)
    matched = []
    for item in observations:
        if platform_id and str(item.get("asset_id") or item.get("platform_id")) == platform_id:
            matched.append(item)
        elif sensor_id and sensor_id.casefold() in str(item.get("sensor_id") or "").casefold():
            matched.append(item)
    return matched


def _render_radar(capture: dict[str, Any], snapshot: dict[str, Any]) -> str:
    observations = _observations_for_capture(capture, snapshot)
    params = capture.get("capture_parameters") if isinstance(capture.get("capture_parameters"), dict) else {}
    product_data = capture.get("product_data") if isinstance(capture.get("product_data"), dict) else {}
    tracks = _list(product_data.get("tracks")) or _list(snapshot.get("fused_tracks") or snapshot.get("tracks"))
    observation_track: dict[str, str] = {}
    for track in tracks:
        track_id = str(track.get("track_id") or track.get("id") or "")
        for observation_id in track.get("source_observation_ids") or []:
            if observation_id and track_id:
                observation_track[str(observation_id)] = track_id
    configured_range_nm = _number(params.get("instrumented_range_nm") or params.get("range_nm"))
    configured_range_km = _number(params.get("instrumented_range_km") or params.get("range_km"))
    observed_ranges = [value for row in observations if (value := _number(row.get("range_nm"))) is not None]
    max_range = configured_range_nm or (
        configured_range_km / 1.852 if configured_range_km is not None else None
    ) or (max(observed_ranges) if observed_ranges else None)
    range_label = (
        _fmt(configured_range_nm, 1, " NM") if configured_range_nm is not None
        else _fmt(configured_range_km, 1, " km") if configured_range_km is not None
        else _fmt(max_range, 1, " NM")
    )
    cx, cy, radius = 286, 306, 166
    rings = "".join(
        f'<circle cx="{cx}" cy="{cy}" r="{radius * fraction:.1f}" class="grid"/>'
        for fraction in (0.25, 0.5, 0.75, 1)
    )
    spokes = "".join(
        f'<line x1="{cx}" y1="{cy}" x2="{cx + radius * math.sin(math.radians(angle)):.1f}" y2="{cy - radius * math.cos(math.radians(angle)):.1f}" class="grid"/>'
        for angle in range(0, 360, 30)
    )
    ring_labels = "".join(
        f'<text x="{cx + radius * fraction + 4:.1f}" y="{cy - 4}" class="tiny">'
        f'{_xml(_fmt(max_range * fraction if max_range else None, 0))}</text>'
        for fraction in (0.25, 0.5, 0.75, 1)
    )
    cardinal_labels = "".join(
        f'<text x="{x}" y="{y}" text-anchor="middle" class="tiny">{label}</text>'
        for label, x, y in (
            ("N", cx, cy - radius - 9), ("E", cx + radius + 12, cy + 4),
            ("S", cx, cy + radius + 16), ("W", cx - radius - 12, cy + 4),
        )
    )
    platform_pose = capture.get("platform_pose") if isinstance(capture.get("platform_pose"), dict) else {}
    heading = _number(platform_pose.get("heading_deg")) or 0.0
    coverage = _number(params.get("azimuth_coverage_deg")) or 360.0
    coverage_svg = ""
    if coverage < 359.9:
        start = heading - coverage / 2.0
        end = heading + coverage / 2.0
        start_x = cx + radius * math.sin(math.radians(start))
        start_y = cy - radius * math.cos(math.radians(start))
        end_x = cx + radius * math.sin(math.radians(end))
        end_y = cy - radius * math.cos(math.radians(end))
        large_arc = 1 if coverage > 180 else 0
        coverage_svg = (
            f'<path d="M {cx} {cy} L {start_x:.1f} {start_y:.1f} '
            f'A {radius} {radius} 0 {large_arc} 1 {end_x:.1f} {end_y:.1f} Z" '
            f'fill="#173a47" fill-opacity="0.24" stroke="#53a9c9" stroke-opacity="0.55"/>'
        )
    sweep_x = cx + radius * math.sin(math.radians(heading))
    sweep_y = cy - radius * math.cos(math.radians(heading))

    times = [value for item in observations if (value := _number(item.get("sim_time"))) is not None]
    latest_time = max(times) if times else None
    current_rows = [
        item for item in observations
        if latest_time is None or _number(item.get("sim_time")) == latest_time
    ]
    observations = sorted(
        observations,
        key=lambda item: (_number(item.get("sim_time")) or 0.0, str(item.get("observation_id") or "")),
    )
    dots = []
    for index, item in enumerate(observations[-60:]):
        bearing = _number(item.get("bearing_deg"))
        range_nm = _number(item.get("range_nm"))
        if bearing is not None and range_nm is not None and max_range and max_range > 0:
            distance = min(radius, radius * range_nm / max_range)
            x = cx + distance * math.sin(math.radians(bearing))
            y = cy - distance * math.cos(math.radians(bearing))
            is_current = latest_time is None or _number(item.get("sim_time")) == latest_time
            opacity = 1.0 if is_current else 0.20 + 0.45 * (index + 1) / max(len(observations), 1)
            fill_colour = "#d8b35d" if is_current else "#53a9c9"
            stroke_colour = "#fff0b5" if is_current else "none"
            dots.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{4.5 if is_current else 2.2}" '
                f'fill="{fill_colour}" fill-opacity="{opacity:.2f}" '
                f'stroke="{stroke_colour}" stroke-width="0.8"/>'
            )

    table = []
    for index, item in enumerate(current_rows[:7]):
        bearing = _number(item.get("bearing_deg"))
        range_nm = _number(item.get("range_nm"))
        snr = _number(item.get("snr_db"))
        sim_time = _number(item.get("sim_time"))
        observation_id = str(item.get("observation_id") or UNAVAILABLE)
        compact_id = observation_id if len(observation_id) <= 18 else "…" + observation_id[-17:]
        track_id = observation_track.get(observation_id) or str(item.get("track_id") or UNAVAILABLE)
        compact_track = track_id if len(track_id) <= 15 else "…" + track_id[-14:]
        rate = _number(item.get("range_rate_kts"))
        y = 174 + index * 39
        table.append(
            f'<line x1="574" y1="{y + 10}" x2="914" y2="{y + 10}" class="grid"/>'
            f'<text x="588" y="{y - 9}" class="tiny">{_xml(compact_id)} · {_xml(compact_track)}</text>'
            f'<text x="588" y="{y + 7}" class="value">{_xml(_fmt(bearing, 1, "°"))} / {_xml(_fmt(range_nm, 1, " NM"))}</text>'
            f'<text x="808" y="{y - 9}" class="tiny">{_xml("T+" + _fmt(sim_time, 0, "s") if sim_time is not None else UNAVAILABLE)}</text>'
            f'<text x="808" y="{y + 7}" class="small">SNR {_xml(_fmt(snr, 1, " dB"))} · ΔR {_xml(_fmt(rate, 1, " kt"))}</text>'
        )
    observation_window = product_data.get("observation_window") if isinstance(product_data.get("observation_window"), dict) else {}
    window_start = _number(observation_window.get("start_sec"))
    window_end = _number(observation_window.get("end_sec"))
    window_label = (
        f"T+{window_start:.0f}s–T+{window_end:.0f}s"
        if window_start is not None and window_end is not None else "单批次"
    )
    resolution_m = _number(params.get("range_resolution_m"))
    snr_values = [value for item in current_rows if (value := _number(item.get("snr_db"))) is not None]
    snr_label = (
        f"{min(snr_values):.1f}–{max(snr_values):.1f} dB" if snr_values else UNAVAILABLE
    )
    empty = '' if observations else '<text x="286" y="310" text-anchor="middle" class="unknown">当前窗口无匹配观测</text>'
    return f"""
      <rect x="32" y="102" width="526" height="406" rx="4" class="panel"/>
      <text x="52" y="126" class="small">PPI / NORTH-UP</text>
      <text x="404" y="126" class="tiny">窗口 {_xml(window_label)}</text>
      {coverage_svg}{rings}{spokes}{ring_labels}{cardinal_labels}
      <line x1="{cx}" y1="{cy}" x2="{sweep_x:.1f}" y2="{sweep_y:.1f}" stroke="#d2a24f" stroke-opacity="0.45"/>
      <circle cx="{cx}" cy="{cy}" r="3" fill="#e3edf1"/>
      {''.join(dots)}{empty}
      <text x="52" y="492" class="small">量程 {_xml(range_label)}</text>
      <text x="205" y="492" class="small">覆盖 {_xml(_fmt(coverage, 0, "°"))}</text>
      <text x="342" y="492" class="small">距离分辨率 {_xml(_fmt(resolution_m, 0, " m"))}</text>
      <rect x="574" y="102" width="354" height="406" rx="4" class="panel"/>
      <text x="588" y="126" class="small">CURRENT PLOTS</text>
      <text x="808" y="126" class="small">当前点迹 {_xml(len(current_rows))}</text>
      {''.join(table)}
      {'' if table else '<text x="751" y="300" text-anchor="middle" class="unknown">当前批次无点迹明细</text>'}
      <line x1="588" y1="458" x2="914" y2="458" class="line"/>
      <text x="588" y="480" class="small">历史点迹 {_xml(max(0, len(observations) - len(current_rows)))}</text>
      <text x="720" y="480" class="small">航迹 {_xml(len(tracks))} · SNR {_xml(snr_label)}</text>
    """


def _render_tracks(capture: dict[str, Any], snapshot: dict[str, Any]) -> str:
    product_data = capture.get("product_data") if isinstance(capture.get("product_data"), dict) else {}
    tracks = _list(product_data.get("tracks")) or _list(snapshot.get("fused_tracks") or snapshot.get("tracks"))
    requested = {str(value) for value in capture.get("track_ids") or [] if value}
    if requested:
        tracks = [row for row in tracks if str(row.get("track_id") or row.get("id")) in requested]
    headers = ("航迹 ID", "域", "位置", "航向", "置信度", "来源")
    x_values = (52, 242, 330, 545, 650, 770)
    header = "".join(f'<text x="{x}" y="128" class="small">{_xml(label)}</text>' for x, label in zip(x_values, headers))
    rows = []
    for index, item in enumerate(tracks[:10]):
        y = 164 + index * 32
        position = _position(item)
        lat, lng = _number(position.get("lat")), _number(position.get("lng"))
        position_text = f"{lat:.4f}, {lng:.4f}" if lat is not None and lng is not None else UNAVAILABLE
        sources = item.get("sources") or item.get("sensor_sources") or item.get("source_observation_ids") or []
        values = (
            item.get("track_id") or item.get("id"), item.get("domain_hint") or item.get("domain"),
            position_text, _fmt(item.get("heading", item.get("heading_deg")), 1, "°"), _fmt(item.get("confidence"), 2),
            ", ".join(str(value) for value in sources) if sources else UNAVAILABLE,
        )
        rows.append('<line x1="44" y1="{0}" x2="916" y2="{0}" class="grid"/>'.format(y + 10) +
                    "".join(f'<text x="{x}" y="{y}" class="txt">{_xml(value)}</text>' for x, value in zip(x_values, values)))
    return f'<rect x="32" y="102" width="896" height="406" rx="4" class="panel"/>{header}{"".join(rows)}' + (
        '' if rows else '<text x="480" y="306" text-anchor="middle" class="unknown">当前快照无融合航迹</text>'
    )


def _render_ais_radar_correlation(capture: dict[str, Any], snapshot: dict[str, Any]) -> str:
    """Render measured AIS/radar candidates without making an identity claim."""
    product_data = capture.get("product_data") if isinstance(capture.get("product_data"), dict) else {}
    observations = _list(product_data.get("observations")) or _observations_for_capture(capture, snapshot)
    tracks = _list(product_data.get("tracks")) or _list(snapshot.get("fused_tracks") or snapshot.get("tracks"))
    parameters = capture.get("capture_parameters") if isinstance(capture.get("capture_parameters"), dict) else {}
    gate = _number(parameters.get("association_gate_nm"))
    by_id = {str(item.get("observation_id")): item for item in observations if item.get("observation_id")}
    rows = []
    paired = radar_only = ais_only = 0
    for index, track in enumerate(tracks[:7]):
        sources = [by_id.get(str(value)) for value in track.get("source_observation_ids") or []]
        sources = [item for item in sources if item]
        radar = next((item for item in sources if "RADAR" in str(item.get("sensor_id") or "").upper()), None)
        ais = next((item for item in sources if "AIS" in str(item.get("sensor_id") or "").upper()), None)
        radar_range, ais_range = _number((radar or {}).get("range_nm")), _number((ais or {}).get("range_nm"))
        radar_bearing, ais_bearing = _number((radar or {}).get("bearing_deg")), _number((ais or {}).get("bearing_deg"))
        delta_range = abs(radar_range - ais_range) if radar_range is not None and ais_range is not None else None
        delta_bearing = (
            abs((radar_bearing - ais_bearing + 180.0) % 360.0 - 180.0)
            if radar_bearing is not None and ais_bearing is not None else None
        )
        if radar and ais:
            paired += 1
            status = "候选关联" if gate is None or (delta_range is not None and delta_range <= gate) else "超出距离门限"
        elif radar:
            radar_only += 1
            status = "仅雷达"
        else:
            ais_only += 1
            status = "仅 AIS"
        y = 185 + index * 42
        track_id = str(track.get("track_id") or track.get("id") or UNAVAILABLE)
        rows.append(
            f'<line x1="48" y1="{y + 12}" x2="912" y2="{y + 12}" class="grid"/>'
            f'<text x="54" y="{y}" class="txt">{_xml(track_id)}</text>'
            f'<text x="244" y="{y}" class="tiny">{_xml(_fmt(radar_bearing, 1, "°"))} / {_xml(_fmt(radar_range, 2, " NM"))}</text>'
            f'<text x="450" y="{y}" class="tiny">{_xml(_fmt(ais_bearing, 1, "°"))} / {_xml(_fmt(ais_range, 2, " NM"))}</text>'
            f'<text x="650" y="{y}" class="tiny">ΔR {_xml(_fmt(delta_range, 2, " NM"))} · ΔB {_xml(_fmt(delta_bearing, 1, "°"))}</text>'
            f'<text x="842" y="{y}" text-anchor="middle" class="value">{_xml(status)}</text>'
        )
    return f"""
      <rect x="32" y="102" width="896" height="406" rx="4" class="panel"/>
      <rect x="48" y="114" width="270" height="42" rx="3" class="panel"/>
      <text x="62" y="132" class="small">雷达 + AIS 候选</text><text x="62" y="150" class="value">{paired}</text>
      <rect x="332" y="114" width="270" height="42" rx="3" class="panel"/>
      <text x="346" y="132" class="small">单源记录</text><text x="346" y="150" class="value">雷达 {radar_only} · AIS {ais_only}</text>
      <rect x="616" y="114" width="296" height="42" rx="3" class="panel"/>
      <text x="630" y="132" class="small">距离关联门限</text><text x="630" y="150" class="value">{_xml(_fmt(gate, 1, " NM"))}</text>
      <text x="54" y="174" class="small">航迹 ID</text><text x="244" y="174" class="small">雷达测量</text>
      <text x="450" y="174" class="small">AIS 报告</text><text x="650" y="174" class="small">测量差</text>
      <text x="842" y="174" text-anchor="middle" class="small">当前关联状态</text>
      {''.join(rows)}
      {'' if rows else '<text x="480" y="310" text-anchor="middle" class="unknown">当前快照无可关联记录</text>'}
      <line x1="48" y1="468" x2="912" y2="468" class="line"/>
      <text x="48" y="490" class="small">候选关联仅表示测量满足门限，不等于身份确认；身份区分须由后端结果和执行 trace 证明。</text>
    """


def _spectrum_samples(
    capture: dict[str, Any],
    observations: list[dict[str, Any]],
) -> list[tuple[float | None, float, float]]:
    """Return measured ``(sim_time, frequency_mhz, power_dbm)`` samples.

    Time is optional because a backend-provided static spectrum can legitimately
    contain frequency bins without acquisition timestamps. The renderer keeps
    those bins visible, but does not invent a timestamp for them.
    """
    product_data = capture.get("product_data") if isinstance(capture.get("product_data"), dict) else {}
    raw = product_data.get("spectrum") or product_data.get("frequency_bins") or []
    samples: list[tuple[float | None, float, float]] = []
    for item in raw:
        if isinstance(item, dict):
            frequency = _number(item.get("frequency_mhz") or item.get("frequency"))
            power = _number(item.get("power_dbm") or item.get("power"))
            sim_time = _number(item.get("sim_time") or item.get("captured_at_sec"))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            frequency, power = _number(item[0]), _number(item[1])
            sim_time = _number(item[2]) if len(item) >= 3 else None
        else:
            continue
        if frequency is not None and power is not None:
            samples.append((sim_time, frequency, power))
    if samples:
        return samples
    for item in observations:
        frequency = _number(item.get("frequency_mhz") or item.get("rf_freq_mhz"))
        power = _number(item.get("power_dbm"))
        if frequency is not None and power is not None:
            samples.append((_number(item.get("sim_time")), frequency, power))
    return samples


def _spectrum_colour(power: float, minimum: float, maximum: float) -> str:
    """Map measured power to a restrained instrument-style colour scale."""
    span = max(maximum - minimum, 1.0)
    ratio = max(0.0, min(1.0, (power - minimum) / span))
    stops = (
        (0.00, (28, 58, 77)),
        (0.35, (31, 111, 139)),
        (0.70, (74, 174, 156)),
        (1.00, (224, 176, 76)),
    )
    for (lower_at, lower), (upper_at, upper) in zip(stops, stops[1:]):
        if ratio <= upper_at:
            local = (ratio - lower_at) / max(upper_at - lower_at, 0.001)
            rgb = tuple(round(a + (b - a) * local) for a, b in zip(lower, upper))
            return "#{:02x}{:02x}{:02x}".format(*rgb)
    return "#e0b04c"


def _render_spectrum(capture: dict[str, Any], snapshot: dict[str, Any]) -> str:
    observations = _observations_for_capture(capture, snapshot)
    samples = _spectrum_samples(capture, observations)
    plot_x, plot_y, plot_w, plot_h = 80, 130, 690, 218
    parameters = capture.get("capture_parameters") if isinstance(capture.get("capture_parameters"), dict) else {}
    configured_band = parameters.get("frequency_band_mhz")
    configured_min = (
        _number(configured_band[0])
        if isinstance(configured_band, (list, tuple)) and len(configured_band) >= 2
        else None
    )
    configured_max = (
        _number(configured_band[1])
        if isinstance(configured_band, (list, tuple)) and len(configured_band) >= 2
        else None
    )
    configured_text = (
        f"{configured_min:.1f}–{configured_max:.1f} MHz"
        if configured_min is not None and configured_max is not None
        else UNAVAILABLE
    )

    measured_frequencies = [frequency for _, frequency, _ in samples]
    measured_powers = [power for _, _, power in samples]
    measured_times = [sim_time for sim_time, _, _ in samples if sim_time is not None]
    if samples:
        measured_min_f, measured_max_f = min(measured_frequencies), max(measured_frequencies)
        min_power, max_power = min(measured_powers), max(measured_powers)
        range_text = f"{measured_min_f:.1f}–{measured_max_f:.1f} MHz"
        power_text = f"{min_power:.1f}–{max_power:.1f} dBm"
    else:
        measured_min_f = measured_max_f = min_power = max_power = None
        range_text = power_text = UNAVAILABLE

    if samples:
        # The receiver configuration may span several GHz while the occupied
        # emissions cover only a narrow portion. Show that measured occupied
        # span at useful scale and state the full configured band separately.
        measured_span = measured_max_f - measured_min_f
        frequency_padding = max(measured_span * 0.08, 5.0)
        axis_min_f = measured_min_f - frequency_padding
        axis_max_f = measured_max_f + frequency_padding
        if configured_min is not None:
            axis_min_f = max(configured_min, axis_min_f)
        if configured_max is not None:
            axis_max_f = min(configured_max, axis_max_f)
        if axis_max_f <= axis_min_f:
            axis_max_f = axis_min_f + 10.0
    elif (
        configured_min is not None and configured_max is not None
        and configured_max > configured_min
    ):
        axis_min_f, axis_max_f = configured_min, configured_max
    else:
        axis_min_f, axis_max_f = 0.0, 1.0
    frequency_span = max(axis_max_f - axis_min_f, 1.0)

    if measured_times:
        min_time, max_time = min(measured_times), max(measured_times)
        time_span = max(max_time - min_time, 1.0)
        window_text = f"T+{min_time:.0f}s–T+{max_time:.0f}s"
    else:
        min_time = max_time = time_span = None
        window_text = UNAVAILABLE

    vertical_grid = []
    frequency_labels = []
    for index in range(5):
        ratio = index / 4
        x = plot_x + ratio * plot_w
        frequency = axis_min_f + ratio * frequency_span
        vertical_grid.append(
            f'<line x1="{x:.1f}" y1="{plot_y}" x2="{x:.1f}" y2="{plot_y + plot_h}" class="grid"/>'
        )
        frequency_labels.append(
            f'<text x="{x:.1f}" y="{plot_y + plot_h + 18}" text-anchor="middle" class="tiny">{frequency:.0f}</text>'
        )

    horizontal_grid = []
    time_labels = []
    for index in range(5):
        ratio = index / 4
        y = plot_y + ratio * plot_h
        horizontal_grid.append(
            f'<line x1="{plot_x}" y1="{y:.1f}" x2="{plot_x + plot_w}" y2="{y:.1f}" class="grid"/>'
        )
        if min_time is not None and max_time is not None:
            sim_time = min_time + ratio * (max_time - min_time)
            time_labels.append(
                f'<text x="{plot_x - 8}" y="{y + 4:.1f}" text-anchor="end" class="tiny">T+{sim_time:.0f}</text>'
            )

    cells = []
    if samples and min_power is not None and max_power is not None:
        known_time_count = max(len({value for value in measured_times}), 1)
        cell_height = max(8.0, min(22.0, plot_h / known_time_count * 0.55))
        for index, (sim_time, frequency, power) in enumerate(samples):
            x = plot_x + (frequency - axis_min_f) / frequency_span * plot_w
            if min_time is not None and sim_time is not None and time_span is not None:
                y = plot_y + (sim_time - min_time) / time_span * plot_h
            else:
                y = plot_y + plot_h / 2 + (index - (len(samples) - 1) / 2) * min(cell_height, 12)
            colour = _spectrum_colour(power, min_power, max_power)
            time_detail = f" / T+{sim_time:.1f}s" if sim_time is not None else ""
            cells.append(
                f'<rect x="{x - 5:.1f}" y="{y - cell_height / 2:.1f}" width="10" height="{cell_height:.1f}" '
                f'rx="1" fill="{colour}" stroke="#b9e3f0" stroke-opacity="0.35">'
                f'<title>{frequency:.2f} MHz / {power:.1f} dBm{time_detail}</title></rect>'
            )

    unique_times = len({round(value, 3) for value in measured_times})
    unique_frequencies = len({round(value, 3) for value in measured_frequencies})
    strongest = max(samples, key=lambda item: item[2]) if samples else None
    strongest_frequency_text = f"{strongest[1]:.1f} MHz" if strongest else UNAVAILABLE
    strongest_power_text = f"{strongest[2]:.1f} dBm" if strongest else UNAVAILABLE
    latest_sample_time = max(measured_times) if measured_times else None
    latest_observations = [
        item for item in observations
        if latest_sample_time is None or _number(item.get("sim_time")) == latest_sample_time
    ]
    latest_details = []
    for index, item in enumerate(latest_observations[:3]):
        frequency = _number(item.get("frequency_mhz") or item.get("rf_freq_mhz"))
        bearing = _number(item.get("bearing_deg"))
        range_nm = _number(item.get("range_nm"))
        latest_details.append(
            f'<text x="790" y="{390 + index * 20}" class="tiny">'
            f'{_xml(_fmt(frequency, 1, " MHz"))} · {_xml(_fmt(bearing, 1, "°"))} · {_xml(_fmt(range_nm, 1, " NM"))}</text>'
        )
    legend_min = min_power if min_power is not None else 0.0
    legend_max = max_power if max_power is not None else 1.0
    legend_span = max(legend_max - legend_min, 1.0)
    legend = "".join(
        f'<rect x="{790 + index * 11}" y="330" width="11" height="12" '
        f'fill="{_spectrum_colour(legend_min + legend_span * index / 9, legend_min, legend_max)}"/>'
        for index in range(10)
    ) if samples else ""
    bearing_samples = [
        (_number(item.get("sim_time")), _number(item.get("bearing_deg")))
        for item in observations
        if _number(item.get("sim_time")) is not None and _number(item.get("bearing_deg")) is not None
    ]
    bearing_trace = ""
    bearing_extent = UNAVAILABLE
    if bearing_samples:
        bearing_times = [item[0] for item in bearing_samples if item[0] is not None]
        bearings = [item[1] for item in bearing_samples if item[1] is not None]
        bearing_min, bearing_max = min(bearings), max(bearings)
        bearing_span = max(bearing_max - bearing_min, 1.0)
        bearing_time_span = max(max(bearing_times) - min(bearing_times), 1.0)
        bearing_points = [
            (
                plot_x + (sample_time - min(bearing_times)) / bearing_time_span * plot_w,
                454 - (bearing - bearing_min) / bearing_span * 48,
            )
            for sample_time, bearing in bearing_samples
            if sample_time is not None and bearing is not None
        ]
        bearing_trace = '<polyline points="{}" class="accent" stroke-width="1.5"/>'.format(
            " ".join(f"{x:.1f},{y:.1f}" for x, y in bearing_points)
        )
        bearing_extent = f"{bearing_min:.1f}°–{bearing_max:.1f}°"
    return f"""
      <rect x="32" y="102" width="896" height="406" rx="4" class="panel"/>
      <rect x="{plot_x}" y="{plot_y}" width="{plot_w}" height="{plot_h}" fill="#081721" stroke="#31505f"/>
      {''.join(vertical_grid)}{''.join(horizontal_grid)}{''.join(frequency_labels)}{''.join(time_labels)}
      {''.join(cells)}
      {'' if samples else '<text x="425" y="278" text-anchor="middle" class="unknown">当前快照未提供频点与功率</text>'}
      <text x="425" y="374" text-anchor="middle" class="tiny">频率 / MHz · 仅绘制实测频点</text>
      <text x="80" y="395" class="small">测向随时间变化</text>
      <rect x="80" y="402" width="690" height="58" fill="#081721" stroke="#31505f"/>
      <line x1="80" y1="431" x2="770" y2="431" class="grid"/>{bearing_trace}
      {'' if bearing_samples else '<text x="425" y="436" text-anchor="middle" class="unknown">当前窗口未提供测向序列</text>'}
      <text x="88" y="476" class="tiny">方位范围 {_xml(bearing_extent)} · 发射源类别需后端判定</text>
      <text x="790" y="142" class="small">观测窗口</text><text x="790" y="164" class="value">{_xml(window_text)}</text>
      <text x="790" y="198" class="small">采样时刻 / 频点</text><text x="790" y="220" class="value">{_xml(unique_times if measured_times else UNAVAILABLE)} / {_xml(unique_frequencies if samples else UNAVAILABLE)}</text>
      <text x="790" y="254" class="small">最强测量</text>
      <text x="790" y="276" class="value">{_xml(strongest_frequency_text)}</text>
      <text x="790" y="298" class="tiny">{_xml(strongest_power_text)}</text>
      <text x="790" y="320" class="small">接收功率</text>
      {legend}
      <text x="790" y="358" class="tiny">低</text><text x="895" y="358" class="tiny">高</text>
      <text x="790" y="378" class="small">当前测向 / 距离</text>{''.join(latest_details)}
      <text x="56" y="498" class="small">测量频率  {_xml(range_text)}</text>
      <text x="300" y="498" class="small">配置频段  {_xml(configured_text)}</text>
      <text x="550" y="498" class="small">功率范围  {_xml(power_text)}</text>
      <text x="830" y="498" class="small">记录  {_xml(len(samples) if samples else UNAVAILABLE)}</text>
    """


def _render_network(capture: dict[str, Any], snapshot: dict[str, Any]) -> str:
    product_data = capture.get("product_data") if isinstance(capture.get("product_data"), dict) else {}
    network = product_data.get("network") if isinstance(product_data.get("network"), dict) else snapshot.get("network")
    network = network if isinstance(network, dict) else {}
    assets = _list(product_data.get("assets")) or _list(snapshot.get("assets") or snapshot.get("own_asset_poses"))
    link_rows = _list(network.get("link_records") or network.get("topology_links"))
    metrics = (
        ("节点", network.get("nodes")), ("有效链路", network.get("links") if not isinstance(network.get("links"), list) else len(network["links"])),
        ("降级链路", network.get("degraded_links")), ("平均质量", _fmt(network.get("avg_quality"), 2)),
        ("网络韧性", _fmt(network.get("resilience"), 1, "%")),
    )
    metric_svg = "".join(
        f'<rect x="{48 + index * 174}" y="112" width="158" height="62" rx="3" class="panel"/>'
        f'<text x="{62 + index * 174}" y="134" class="small">{_xml(label)}</text>'
        f'<text x="{62 + index * 174}" y="160" class="value">{_xml(_text(value))}</text>'
        for index, (label, value) in enumerate(metrics)
    )
    node_positions: dict[str, tuple[float, float]] = {}
    nodes_svg = []
    for index, asset in enumerate(assets[:12]):
        asset_id = str(asset.get("id") or asset.get("asset_id") or f"NODE-{index + 1}")
        col, row = index % 4, index // 4
        x, y = 130 + col * 225, 238 + row * 92
        node_positions[asset_id] = (x, y)
        health = asset.get("health") if isinstance(asset.get("health"), dict) else {}
        comms = asset.get("comms_strength", health.get("comms_strength"))
        status = asset.get("status") or UNAVAILABLE
        nodes_svg.append(
            f'<rect x="{x - 76}" y="{y - 25}" width="152" height="50" rx="4" class="panel"/>'
            f'<text x="{x}" y="{y - 3}" text-anchor="middle" class="txt">{_xml(asset_id)}</text>'
            f'<text x="{x}" y="{y + 15}" text-anchor="middle" class="tiny">{_xml(status)} · 通信 {_xml(_fmt(comms, 0, "%"))}</text>'
        )
    edges_svg = []
    for link in link_rows:
        source = str(link.get("from") or link.get("source") or "")
        target = str(link.get("to") or link.get("target") or "")
        if source in node_positions and target in node_positions:
            x1, y1 = node_positions[source]
            x2, y2 = node_positions[target]
            quality = _number(link.get("quality"))
            link_status = str(link.get("status") or "measured").casefold()
            if link_status in {"unavailable", "disconnected", "failed"}:
                colour = "#b76565"
            elif link_status in {"degraded", "limited", "unstable"}:
                colour = "#d2a24f"
            else:
                colour = "#55b58d" if link_status in {"active", "healthy", "available", "nominal"} else "#53a9c9"
            opacity = 0.35 + 0.65 * max(0.0, min(1.0, quality if quality is not None else 0.5))
            width = 0.8 + 2.2 * max(0.0, min(1.0, quality if quality is not None else 0.5))
            edges_svg.append(
                f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{colour}" '
                f'stroke-opacity="{opacity:.2f}" stroke-width="{width:.1f}"><title>{_xml(source)}→{_xml(target)} · Q {_xml(_fmt(quality, 2))}</title></line>'
            )
    unavailable = '' if link_rows else '<text x="480" y="492" text-anchor="middle" class="small">链路拓扑明细  未提供</text>'
    legend = '<text x="48" y="498" class="tiny">颜色按链路状态字段 · 线宽和透明度按实测质量 · 未设置额外判定门限</text>'
    return metric_svg + ''.join(edges_svg) + ''.join(nodes_svg) + unavailable + legend


def _render_link_monitor(capture: dict[str, Any], snapshot: dict[str, Any]) -> str:
    product_data = capture.get("product_data") if isinstance(capture.get("product_data"), dict) else {}
    network = product_data.get("network") if isinstance(product_data.get("network"), dict) else {}
    samples = _list(network.get("history_records"))
    specifications = (
        ("rssi_dbm", "RSSI", "dBm"),
        ("snr_db", "SNR", "dB"),
        ("latency_ms", "时延", "ms"),
        ("packet_loss_pct", "丢包", "%"),
    )
    panels = []
    for index, (key, label, unit) in enumerate(specifications):
        values: list[tuple[float, float, float, float]] = []
        for sample in samples:
            sim_time = _number(sample.get("sim_time"))
            readings = [
                value for link in _list(sample.get("links"))
                if (value := _number(link.get(key))) is not None
            ]
            if sim_time is not None and readings:
                values.append((sim_time, sum(readings) / len(readings), min(readings), max(readings)))
        col, row = index % 2, index // 2
        x, y, width, height = 40 + col * 456, 104 + row * 186, 424, 170
        plot_x, plot_y, plot_w, plot_h = x + 18, y + 45, width - 36, height - 66
        polyline = ""
        latest = UNAVAILABLE
        extent = UNAVAILABLE
        if values:
            times = [item[0] for item in values]
            readings = [item[1] for item in values]
            all_min = min(item[2] for item in values)
            all_max = max(item[3] for item in values)
            min_time, max_time = min(times), max(times)
            min_value, max_value = min(readings), max(readings)
            time_span = max(max_time - min_time, 1.0)
            value_span = max(max_value - min_value, 1e-6)
            points = [
                (
                    plot_x + (sample_time - min_time) / time_span * plot_w,
                    plot_y + plot_h - (reading - min_value) / value_span * plot_h,
                )
                for sample_time, reading, _, _ in values
            ]
            polyline = '<polyline points="{}" class="accent" stroke-width="2"/>'.format(
                " ".join(f"{px:.1f},{py:.1f}" for px, py in points)
            )
            latest = _fmt(readings[-1], 1, f" {unit}")
            extent = f"链路均值 · T+{min_time:.0f}–{max_time:.0f}s · 实测范围 {all_min:.1f}–{all_max:.1f} {unit}"
        panels.append(f'''
          <rect x="{x}" y="{y}" width="{width}" height="{height}" rx="4" class="panel"/>
          <text x="{x + 18}" y="{y + 25}" class="small">{_xml(label)}</text>
          <text x="{x + width - 18}" y="{y + 25}" text-anchor="end" class="value">{_xml(latest)}</text>
          <line x1="{plot_x}" y1="{plot_y + plot_h}" x2="{plot_x + plot_w}" y2="{plot_y + plot_h}" class="grid"/>
          {polyline}
          {'' if values else f'<text x="{x + width / 2}" y="{y + 103}" text-anchor="middle" class="unknown">当前快照未提供链路时序</text>'}
          <text x="{x + 18}" y="{y + height - 8}" class="tiny">{_xml(extent)}</text>
        ''')
    current_links = _list(network.get("link_records") or network.get("topology_links"))
    worst = min(
        current_links,
        key=lambda item: _number(item.get("quality")) if _number(item.get("quality")) is not None else 1e9,
        default=None,
    )
    worst_label = UNAVAILABLE
    if worst:
        source = worst.get("from") or worst.get("source")
        target = worst.get("to") or worst.get("target")
        worst_label = (
            f"{source}→{target} · Q {_fmt(worst.get('quality'), 2)} · "
            f"RSSI {_fmt(worst.get('rssi_dbm'), 1, ' dBm')} · 丢包 {_fmt(worst.get('packet_loss_pct'), 1, '%')}"
        )
    monitor = network.get("monitor_platform_id") or capture.get("platform_id")
    return (
        ''.join(panels)
        + f'<text x="40" y="485" class="small">当前最低质量链路  {_xml(worst_label)}</text>'
        + f'<text x="928" y="508" text-anchor="end" class="small">监测节点  {_xml(monitor)}</text>'
    )


def _render_resources(capture: dict[str, Any], snapshot: dict[str, Any]) -> str:
    product_data = capture.get("product_data") if isinstance(capture.get("product_data"), dict) else {}
    assets = _list(product_data.get("assets")) or _list(snapshot.get("assets") or snapshot.get("own_asset_poses"))
    headers = ("平台", "角色", "状态", "速度", "能源", "通信")
    x_values = (52, 210, 452, 565, 680, 790)
    header = "".join(f'<text x="{x}" y="128" class="small">{_xml(label)}</text>' for x, label in zip(x_values, headers))
    rows = []
    for index, item in enumerate(assets[:10]):
        y = 164 + index * 32
        health = item.get("health") if isinstance(item.get("health"), dict) else {}
        values = (
            item.get("id") or item.get("asset_id") or item.get("platform_id"), item.get("role"), item.get("status"),
            _fmt(item.get("speed_kts"), 1, " kt"),
            _fmt(item.get("battery_pct", health.get("battery_pct", health.get("fuel_pct"))), 0, "%"),
            _fmt(item.get("comms_strength", health.get("comms_strength")), 0, "%"),
        )
        rows.append('<line x1="44" y1="{0}" x2="916" y2="{0}" class="grid"/>'.format(y + 10) +
                    "".join(f'<text x="{x}" y="{y}" class="txt">{_xml(value)}</text>' for x, value in zip(x_values, values)))
    return f'<rect x="32" y="102" width="896" height="406" rx="4" class="panel"/>{header}{"".join(rows)}' + (
        '' if rows else '<text x="480" y="306" text-anchor="middle" class="unknown">当前平台状态未提供</text>'
    )


def _render_execution(capture: dict[str, Any], snapshot: dict[str, Any]) -> str:
    clock = snapshot.get("clock") if isinstance(snapshot.get("clock"), dict) else {}
    tasks = _list(snapshot.get("tasks"))
    alerts = _list(snapshot.get("alerts"))
    product_data = capture.get("product_data") if isinstance(capture.get("product_data"), dict) else {}
    workflow = product_data.get("workflow") if isinstance(product_data.get("workflow"), dict) else {}
    activities = _list(product_data.get("activities")) or tasks
    metrics = (
        ("后端工作流", workflow.get("status")), ("Workflow ID", workflow.get("workflow_id")),
        ("已投影评估", workflow.get("applied_assessment_count")),
        ("活动记录", len(activities) if activities else None),
    )
    metric_svg = "".join(
        f'<rect x="{48 + index * 218}" y="112" width="200" height="66" rx="3" class="panel"/>'
        f'<text x="{62 + index * 218}" y="136" class="small">{_xml(label)}</text>'
        f'<text x="{62 + index * 218}" y="162" class="value">{_xml(_text(value))}</text>'
        for index, (label, value) in enumerate(metrics)
    )
    rows = []
    for index, item in enumerate(activities[:7]):
        y = 222 + index * 38
        rows.append(
            f'<rect x="48" y="{y - 24}" width="864" height="31" rx="3" class="panel"/>'
            f'<text x="62" y="{y - 4}" class="tiny">{index + 1:02d}</text>'
            f'<text x="104" y="{y - 4}" class="txt">{_xml(item.get("title") or item.get("name") or item.get("task_id") or item.get("id"))}</text>'
            f'<text x="730" y="{y - 4}" class="small">{_xml(item.get("status"))}</text>'
        )
    return metric_svg + ''.join(rows) + (
        '' if rows else '<text x="480" y="328" text-anchor="middle" class="unknown">当前执行活动明细未提供</text>'
    )


RENDERERS = {
    "radar_ppi": _render_radar,
    "track_table": _render_tracks,
    "ais_radar_correlation": _render_ais_radar_correlation,
    "elint_spectrum": _render_spectrum,
    "network_topology": _render_network,
    "link_monitor": _render_link_monitor,
    "resource_status": _render_resources,
    "execution_state": _render_execution,
}


def render_evidence_product(capture_record: dict[str, Any], frozen_state: dict[str, Any]) -> dict[str, Any]:
    """Return deterministic SVG and metadata for one public capture record."""
    capture = {key: deepcopy(value) for key, value in capture_record.items() if key in CAPTURE_KEYS or key == "renderer_type"}
    renderer_type = capture.get("renderer_type") or resolve_renderer_type(capture)
    if renderer_type not in RENDERERS:
        raise ValueError(f"unsupported evidence renderer: {renderer_type or UNAVAILABLE}")
    snapshot = {
        key: deepcopy(frozen_state.get(key))
        for key in ("clock", "observations", "network", "assets", "own_asset_poses", "fused_tracks", "tracks", "tasks", "alerts")
        if frozen_state.get(key) is not None
    }
    body = RENDERERS[str(renderer_type)](capture, snapshot)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{SVG_WIDTH}" height="{SVG_HEIGHT}" viewBox="0 0 {SVG_WIDTH} {SVG_HEIGHT}" role="img">'
        f'<style>{_style()}</style><rect width="{SVG_WIDTH}" height="{SVG_HEIGHT}" class="bg"/>'
        f'{_header(capture, snapshot, str(renderer_type))}{body}'
        f'<text x="928" y="526" text-anchor="end" class="tiny">AMOS CURRENT-STATE PRODUCT · {_xml(capture.get("capture_id"))}</text></svg>'
    )
    encoded = svg.encode("utf-8")
    checksum = sha256(encoded).hexdigest()
    return {
        "renderer_type": renderer_type,
        "mime_type": "image/svg+xml",
        "svg": svg,
        "bytes": encoded,
        "checksum": {"algorithm": "sha256", "value": checksum},
    }


@dataclass(frozen=True)
class StoredEvidenceProduct:
    run_id: str
    media_id: str
    snapshot_token: str
    content: bytes
    checksum: str


class EvidenceProductService:
    """Freeze rendered products behind run-scoped, causal product URIs."""

    def __init__(self, *, max_products: int = 256):
        self.max_products = max(1, int(max_products))
        self._products: OrderedDict[tuple[str, str, str], StoredEvidenceProduct] = OrderedDict()
        self._lock = threading.RLock()

    @staticmethod
    def _frozen_snapshot(operator_state: dict[str, Any], agent_state: dict[str, Any]) -> dict[str, Any]:
        return {
            "clock": deepcopy(operator_state.get("clock") or {}),
            "assets": deepcopy(operator_state.get("assets") or []),
            "fused_tracks": deepcopy(operator_state.get("fused_tracks") or []),
            "network": deepcopy(operator_state.get("network") or {}),
            "tasks": deepcopy(operator_state.get("tasks") or []),
            "alerts": deepcopy(operator_state.get("alerts") or []),
            "observations": deepcopy(agent_state.get("observations") or []),
        }

    def freeze_manifest(
        self,
        operator_state: dict[str, Any],
        agent_state: dict[str, Any],
        scenario: dict[str, Any],
    ) -> dict[str, Any]:
        clock = operator_state.get("clock") or {}
        run_id = str(clock.get("run_id") or "")
        if not run_id:
            return {"run_id": None, "snapshot_at_sec": None, "products": []}
        elapsed = float(clock.get("elapsed_sec", 0) or 0)
        snapshot_token = str(max(0, round(elapsed * 1000)))
        story = operator_state.get("scenario_story") or {}
        captures_by_media = {
            str(item.get("media_id")): item
            for item in scenario.get("capture_plans") or []
            if isinstance(item, dict) and item.get("media_id")
        }
        frozen = self._frozen_snapshot(operator_state, agent_state)
        products = []
        for media in story.get("media_cues") or []:
            if not isinstance(media, dict) or not media.get("media_id"):
                continue
            capture = build_public_capture_record(media, captures_by_media.get(str(media["media_id"])), frozen)
            renderer_type = capture.get("renderer_type")
            is_svg = str(media.get("mime_type") or media.get("media_type") or "").lower() == "image/svg+xml"
            if not renderer_type or not is_svg:
                continue
            if capture.get("dynamic_uri") and capture.get("dynamic_checksum"):
                product_data = capture.get("product_data") if isinstance(capture.get("product_data"), dict) else {}
                products.append({
                    "media_id": str(media["media_id"]),
                    "capture_id": capture.get("capture_id"),
                    "renderer_type": renderer_type,
                    "uri": capture["dynamic_uri"],
                    "captured_at_sec": capture.get("captured_at_sec"),
                    "snapshot_at_sec": capture.get("captured_at_sec"),
                    "platform_id": capture.get("platform_id"),
                    "sensor_instance_id": capture.get("sensor_instance_id"),
                    "capability_id": capture.get("capability_id"),
                    "observation_ids": list(capture.get("observation_ids") or []),
                    "track_ids": list(capture.get("track_ids") or []),
                    "bearing_summary_deg": product_data.get("bearing_summary_deg"),
                    "range_summary_nm": product_data.get("range_summary_nm"),
                    "checksum": {"algorithm": "sha256", "value": str(capture["dynamic_checksum"])},
                })
                continue
            rendered = render_evidence_product(capture, frozen)
            media_id = str(media["media_id"])
            key = (run_id, media_id, snapshot_token)
            stored = StoredEvidenceProduct(
                run_id=run_id,
                media_id=media_id,
                snapshot_token=snapshot_token,
                content=rendered["bytes"],
                checksum=str(rendered["checksum"]["value"]),
            )
            with self._lock:
                self._products[key] = stored
                self._products.move_to_end(key)
                while len(self._products) > self.max_products:
                    self._products.popitem(last=False)
            product_data = capture.get("product_data") if isinstance(capture.get("product_data"), dict) else {}
            products.append({
                "media_id": media_id,
                "capture_id": capture.get("capture_id"),
                "renderer_type": renderer_type,
                "uri": f"/api/v1/evidence-products/{quote(run_id, safe='')}/{quote(media_id, safe='')}/{snapshot_token}.svg",
                "captured_at_sec": capture.get("captured_at_sec"),
                "snapshot_at_sec": elapsed,
                "platform_id": capture.get("platform_id"),
                "sensor_instance_id": capture.get("sensor_instance_id"),
                "capability_id": capture.get("capability_id"),
                "observation_ids": list(capture.get("observation_ids") or []),
                "track_ids": list(capture.get("track_ids") or []),
                "bearing_summary_deg": product_data.get("bearing_summary_deg"),
                "range_summary_nm": product_data.get("range_summary_nm"),
                "checksum": deepcopy(rendered["checksum"]),
            })
        return {"run_id": run_id, "snapshot_at_sec": elapsed, "products": products}

    def freeze_capture_record(
        self,
        capture: dict[str, Any],
        frozen_state: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Render and retain one immutable product at its actual capture tick."""
        run_id = str(capture.get("run_id") or "")
        media_id = str(capture.get("media_id") or "")
        if not run_id or not media_id or not resolve_renderer_type(capture):
            return None
        captured_at = _number(capture.get("captured_at_sim_time", capture.get("at_sec")))
        snapshot_token = str(max(0, round((captured_at or 0.0) * 1000)))
        rendered = render_evidence_product(capture, frozen_state)
        checksum = str(rendered["checksum"]["value"])
        stored = StoredEvidenceProduct(
            run_id=run_id,
            media_id=media_id,
            snapshot_token=snapshot_token,
            content=rendered["bytes"],
            checksum=checksum,
        )
        key = (run_id, media_id, snapshot_token)
        with self._lock:
            self._products[key] = stored
            self._products.move_to_end(key)
            while len(self._products) > self.max_products:
                self._products.popitem(last=False)
        return {
            "uri": f"/api/v1/evidence-products/{quote(run_id, safe='')}/{quote(media_id, safe='')}/{snapshot_token}.svg",
            "checksum": {"algorithm": "sha256", "value": checksum},
            "snapshot_token": snapshot_token,
            "renderer_type": rendered["renderer_type"],
        }

    def get(self, run_id: str, media_id: str, snapshot_token: str) -> StoredEvidenceProduct | None:
        key = (str(run_id), str(media_id), str(snapshot_token))
        with self._lock:
            product = self._products.get(key)
            if product:
                self._products.move_to_end(key)
            return product


_EVIDENCE_PRODUCT_SERVICE = EvidenceProductService()


def get_evidence_product_service() -> EvidenceProductService:
    return _EVIDENCE_PRODUCT_SERVICE


__all__ = [
    "EvidenceProductService",
    "StoredEvidenceProduct",
    "build_public_capture_record",
    "get_evidence_product_service",
    "render_evidence_product",
    "resolve_renderer_type",
]
