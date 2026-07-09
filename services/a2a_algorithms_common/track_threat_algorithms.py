"""Track-threat algorithm package functions for the A2A algorithm library."""
from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

try:
    import torch
except ImportError:  # pragma: no cover
    torch = None  # type: ignore[assignment]


EARTH_RADIUS_M = 6_371_000.0
OBJECT_TYPES = ("aircraft", "ship", "uav", "unknown")


def target_type_classifier(inputs: dict, _params: dict) -> dict:
    observations = list(inputs.get("observations") or inputs.get("detections") or inputs.get("tracks") or [])
    if not observations:
        raise ValueError("observations, detections, or tracks is required")
    classifications = []
    for index, observation in enumerate(observations):
        object_type = _normalize_object_type(observation.get("object_type"))
        confidence = _clamp(float(observation.get("confidence", 0.75)), 0.0, 1.0)
        speed = float(observation.get("speed", 0.0) or 0.0)
        alt = float(observation.get("alt", 0.0) or 0.0)
        type_scores = _type_scores(object_type, confidence, speed, alt)
        best_type = max(type_scores, key=type_scores.get)
        classifications.append(
            {
                "item_id": str(observation.get("detection_id") or observation.get("track_id") or f"obs-{index+1:03d}"),
                "object_type": best_type,
                "confidence": round(type_scores[best_type], 4),
                "type_scores": type_scores,
                "evidence": [
                    f"输入类型为 {object_type}",
                    f"速度 {round(speed, 2)} m/s，高度 {round(alt, 2)} m",
                    "该算法用于结构化目标类型补全，不做真实敌我识别。",
                ],
            }
        )
    return {
        "schema_version": "target_type_classifier/v1",
        "classifications": classifications,
    }


def trajectory_predictor(inputs: dict, params: dict) -> dict:
    tracks = list(inputs.get("tracks") or [])
    if not tracks:
        raise ValueError("tracks is required")
    horizons = [int(value) for value in params.get("horizons_s") or inputs.get("horizons_s") or [10, 20, 30, 60]]
    model_summary = _model_summary()
    predictions = []
    for track in tracks:
        object_type = _normalize_object_type(track.get("object_type"))
        model_status = _model_status_for(object_type, model_summary)
        baseline_model = "IMM" if object_type in {"aircraft", "uav", "unknown"} else "CV"
        inference = _run_st_gnn_prediction(track, object_type, horizons, model_summary)
        if inference["used"]:
            model_family = "st_gnn"
            predicted_path = inference["predicted_path"]
            fallback_used = False
            fallback_reason = ""
            latency_ms = inference["latency_ms"]
        else:
            model_family = "physics_baseline"
            predicted_path = [_predict_point(track, horizon) for horizon in horizons]
            fallback_used = True
            fallback_reason = inference["reason"]
            latency_ms = 0.0
        predictions.append(
            {
                "track_id": str(track.get("track_id") or track.get("detection_id") or "track-unknown"),
                "object_type": object_type,
                "model_family": model_family,
                "model_version": _model_version_for(object_type, model_summary),
                "model_status": model_status,
                "baseline_model": baseline_model,
                "fallback_used": fallback_used,
                "fallback_reason": fallback_reason,
                "predicted_path": predicted_path,
                "inference_latency_ms": latency_ms,
                "model_runtime": {
                    "backend": "torchscript" if inference["used"] else "physics_baseline",
                    "used": bool(inference["used"]),
                    "fallback": not bool(inference["used"]),
                    "reason": inference["reason"],
                },
                "safety_boundary": "Simulation trajectory prediction only; no weapon control or engagement decision.",
            }
        )
    return {
        "schema_version": "trajectory_predictor/v1",
        "predictions": predictions,
        "model_summary": model_summary,
    }


def multimodal_feature_fuser(inputs: dict, _params: dict) -> dict:
    detections = list(inputs.get("detections") or [])
    tracks = list(inputs.get("tracks") or [])
    scene = dict(inputs.get("scene") or {})
    protected_assets = list(scene.get("protected_assets") or inputs.get("protected_assets") or [])
    feature_vectors = []
    for track in tracks or detections:
        speed = float(track.get("speed", 0.0) or 0.0)
        confidence = _clamp(float(track.get("confidence", 0.75)), 0.0, 1.0)
        feature_vectors.append(
            {
                "item_id": str(track.get("track_id") or track.get("detection_id") or "item-unknown"),
                "object_type": _normalize_object_type(track.get("object_type")),
                "numeric_features": {
                    "speed_mps": speed,
                    "heading_sin": round(math.sin(math.radians(float(track.get("heading", 0.0) or 0.0))), 6),
                    "heading_cos": round(math.cos(math.radians(float(track.get("heading", 0.0) or 0.0))), 6),
                    "alt_m": float(track.get("alt", 0.0) or 0.0),
                    "confidence": confidence,
                    "protected_asset_count": len(protected_assets),
                },
            }
        )
    return {
        "schema_version": "multimodal_feature_fuser/v1",
        "feature_version": "track_threat_features_v1",
        "feature_vectors": feature_vectors,
        "counts": {
            "detections": len(detections),
            "tracks": len(tracks),
            "protected_assets": len(protected_assets),
        },
        "warnings": [],
    }


def track_state_updater(inputs: dict, _params: dict) -> dict:
    detections = list(inputs.get("detections") or [])
    existing_tracks = list(inputs.get("existing_tracks") or [])
    if not detections and not existing_tracks:
        raise ValueError("detections or existing_tracks is required")
    tracks = [dict(track) for track in existing_tracks]
    updates = []
    for detection in detections:
        matched = _match_track(detection, tracks)
        if matched is None:
            track = _track_from_detection(detection)
            tracks.append(track)
            update_type = "created"
        else:
            track = _update_track(matched, detection)
            update_type = "updated"
        updates.append({"track_id": track["track_id"], "update_type": update_type})
    return {
        "schema_version": "track_state_updater/v1",
        "tracks": tracks,
        "updates": updates,
        "summary": {
            "updated_count": len(updates),
            "active_track_count": len(tracks),
        },
    }


def graph_relation_reasoner(inputs: dict, params: dict) -> dict:
    tracks = list(inputs.get("tracks") or [])
    if not tracks:
        raise ValueError("tracks is required")
    max_distance_m = float(params.get("max_distance_m", 8_000.0))
    max_heading_delta = float(params.get("max_heading_delta_deg", 25.0))
    max_speed_delta = float(params.get("max_speed_delta_mps", 80.0))
    relations = []
    for i, left in enumerate(tracks):
        for right in tracks[i + 1 :]:
            distance = haversine_m(float(left["lat"]), float(left["lon"]), float(right["lat"]), float(right["lon"]))
            heading_delta = heading_difference(float(left.get("heading", 0.0)), float(right.get("heading", 0.0)))
            speed_delta = abs(float(left.get("speed", 0.0) or 0.0) - float(right.get("speed", 0.0) or 0.0))
            if distance <= max_distance_m and heading_delta <= max_heading_delta and speed_delta <= max_speed_delta:
                relations.append(
                    {
                        "source_track_id": str(left.get("track_id") or left.get("detection_id")),
                        "target_track_id": str(right.get("track_id") or right.get("detection_id")),
                        "distance_m": round(distance, 3),
                        "heading_delta_deg": round(heading_delta, 3),
                        "speed_delta_mps": round(speed_delta, 3),
                        "relation_score": round(1.0 - min(distance / max_distance_m, 1.0) * 0.5, 4),
                    }
                )
    groups = _connected_groups(tracks, relations)
    return {
        "schema_version": "graph_relation_reasoner/v1",
        "relations": relations,
        "groups": groups,
        "graph_summary": {
            "node_count": len(tracks),
            "edge_count": len(relations),
            "group_count": len(groups),
        },
    }


def _model_summary() -> dict:
    root = Path(__file__).resolve().parents[2]
    aircraft_dir = root / "models/track_threat/st_gnn_aircraft_kaggle_v1_candidate"
    ship_dir = root / "models/track_threat/st_gnn_ship_kaggle_v1"
    return {
        "aircraft_model_available": aircraft_dir.exists(),
        "ship_model_available": ship_dir.exists(),
        "aircraft_release_status": "candidate_ade_gate_pending" if aircraft_dir.exists() else "unavailable",
        "ship_release_status": "gate_passed" if ship_dir.exists() else "unavailable",
    }


def _run_st_gnn_prediction(track: dict, object_type: str, requested_horizons: Sequence[int], summary: dict) -> dict:
    start = time.perf_counter()
    if torch is None:
        return _model_not_used("PyTorch is not installed")
    bundle_dir = _bundle_dir_for(object_type, summary)
    if bundle_dir is None:
        return _model_not_used("no frozen ST-GNN bundle is available for object_type")
    try:
        manifest = _read_json(bundle_dir / "model_manifest.json")
        normalization = _read_json(bundle_dir / "normalization.json")
        metrics = _read_json(bundle_dir / "metrics.json")
        model_horizons = [int(value) for value in manifest["prediction_horizons_s"]]
        selected_horizons = [int(value) for value in requested_horizons if int(value) in set(model_horizons)]
        if not selected_horizons:
            return _model_not_used("requested horizons do not match frozen model horizons")
        tensors = _build_st_gnn_tensors(track, model_horizons, normalization, object_type)
        model = _load_torchscript_model(bundle_dir)
        with torch.no_grad():
            _residual_mean, log_sigma, prediction = model(
                tensors["history_features"],
                tensors["edge_index"],
                tensors["edge_features"],
                tensors["physics_baseline"],
            )
        prediction = prediction.detach().cpu()
        log_sigma = log_sigma.detach().cpu()
        horizon_to_index = {horizon: index for index, horizon in enumerate(model_horizons)}
        sigma_scale = float(
            metrics.get("st_gnn", {})
            .get("uncertainty_calibration", metrics.get("uncertainty_calibration", {}))
            .get("sigma_scale", 1.0)
        )
        predicted_path = []
        for horizon in selected_horizons:
            index = horizon_to_index[horizon]
            east_m = float(prediction[0, index, 0].item())
            north_m = float(prediction[0, index, 1].item())
            lat, lon = offset_to_lat_lon(
                float(track.get("lat", 0.0) or 0.0),
                float(track.get("lon", 0.0) or 0.0),
                east_m,
                north_m,
            )
            sigma_e = math.exp(float(log_sigma[0, index, 0].item())) * sigma_scale
            sigma_n = math.exp(float(log_sigma[0, index, 1].item())) * sigma_scale
            uncertainty = math.sqrt(sigma_e * sigma_e + sigma_n * sigma_n)
            predicted_path.append(
                {
                    "horizon_s": horizon,
                    "lat": round(lat, 7),
                    "lon": round(lon, 7),
                    "alt": float(track.get("alt", 0.0) or 0.0),
                    "uncertainty_radius_m": round(max(25.0, uncertainty), 3),
                    "prediction_confidence": round(_confidence_from_uncertainty(uncertainty), 4),
                    "offset_east_m": round(east_m, 3),
                    "offset_north_m": round(north_m, 3),
                }
            )
        return {
            "used": True,
            "reason": "",
            "predicted_path": predicted_path,
            "latency_ms": round((time.perf_counter() - start) * 1000.0, 3),
        }
    except Exception as exc:  # pragma: no cover - covered through fallback behavior
        return _model_not_used(f"TorchScript inference failed: {type(exc).__name__}: {exc}")


def _bundle_dir_for(object_type: str, summary: dict) -> Path | None:
    root = Path(__file__).resolve().parents[2]
    if object_type == "aircraft" and summary["aircraft_model_available"]:
        return root / "models/track_threat/st_gnn_aircraft_kaggle_v1_candidate"
    if object_type == "ship" and summary["ship_model_available"]:
        return root / "models/track_threat/st_gnn_ship_kaggle_v1"
    return None


def _load_torchscript_model(bundle_dir: Path):
    return torch.jit.load(str(bundle_dir / "model.ts"), map_location="cpu").eval()


def _read_json(path: Path) -> dict:
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def _model_not_used(reason: str) -> dict:
    return {
        "used": False,
        "reason": reason,
        "predicted_path": [],
        "latency_ms": 0.0,
    }


def _build_st_gnn_tensors(track: dict, horizons: Sequence[int], normalization: dict, object_type: str) -> dict:
    history = _prepare_history(track, history_points=6)
    anchor = history[-1]
    node_rows = [_node_feature_row(point, anchor) for point in history]
    node_rows = _normalize_rows(node_rows, normalization["node_mean"], normalization["node_std"])
    physics_baseline = [_physics_offset(track, horizon) for horizon in horizons]
    history_features = torch.tensor([node_rows], dtype=torch.float32)
    physics_baseline_tensor = torch.tensor([physics_baseline], dtype=torch.float32)
    if object_type == "ship":
        edge_index = torch.tensor([[0], [0]], dtype=torch.long)
        edge_features = torch.tensor(
            _normalize_rows([[0.0, 0.0, 1.0, 0.0, 1.0, 0.0, 0.0, 1.0]], normalization["edge_mean"], normalization["edge_std"]),
            dtype=torch.float32,
        )
    else:
        edge_index = torch.zeros((2, 0), dtype=torch.long)
        edge_features = torch.zeros((0, 8), dtype=torch.float32)
    return {
        "history_features": history_features,
        "edge_index": edge_index,
        "edge_features": edge_features,
        "physics_baseline": physics_baseline_tensor,
    }


def _prepare_history(track: dict, history_points: int) -> List[dict]:
    history = list(track.get("history_path") or [])
    if not history:
        history = [track]
    history = sorted(history, key=lambda item: float(item.get("timestamp", 0.0) or 0.0))
    history = history[-history_points:]
    while len(history) < history_points:
        history.insert(0, dict(history[0]))
    return history


def _node_feature_row(point: dict, anchor: dict) -> List[float]:
    east, north = lat_lon_to_offset(
        float(anchor.get("lat", 0.0) or 0.0),
        float(anchor.get("lon", 0.0) or 0.0),
        float(point.get("lat", 0.0) or 0.0),
        float(point.get("lon", 0.0) or 0.0),
    )
    heading = math.radians(float(point.get("heading", anchor.get("heading", 0.0)) or 0.0))
    return [
        east,
        north,
        float(point.get("timestamp", 0.0) or 0.0) - float(anchor.get("timestamp", 0.0) or 0.0),
        float(point.get("speed", anchor.get("speed", 0.0)) or 0.0),
        math.sin(heading),
        math.cos(heading),
        float(point.get("alt", anchor.get("alt", 0.0)) or 0.0) - float(anchor.get("alt", 0.0) or 0.0),
        _clamp(float(point.get("confidence", anchor.get("confidence", 0.75)) or 0.75), 0.0, 1.0),
        1.0,
    ]


def _normalize_rows(rows: Sequence[Sequence[float]], mean: Sequence[float], std: Sequence[float]) -> List[List[float]]:
    normalized = []
    for row in rows:
        normalized.append(
            [
                (float(value) - float(mean[index])) / max(float(std[index]), 1e-6)
                for index, value in enumerate(row)
            ]
        )
    return normalized


def _physics_offset(track: dict, horizon_s: int) -> List[float]:
    speed = float(track.get("speed", 0.0) or 0.0)
    heading = math.radians(float(track.get("heading", 0.0) or 0.0))
    distance = speed * float(horizon_s)
    return [distance * math.sin(heading), distance * math.cos(heading)]


def _confidence_from_uncertainty(uncertainty_m: float) -> float:
    return _clamp(1.0 / (1.0 + uncertainty_m / 2_000.0), 0.1, 0.99)


def _model_version_for(object_type: str, summary: dict) -> str:
    if object_type == "aircraft" and summary["aircraft_model_available"]:
        return "st_gnn_aircraft_kaggle_v1_candidate"
    if object_type == "ship" and summary["ship_model_available"]:
        return "st_gnn_ship_kaggle_v1"
    return "physics_baseline_v1"


def _model_status_for(object_type: str, summary: dict) -> str:
    if object_type == "aircraft" and summary["aircraft_model_available"]:
        return "bundle_available_candidate_ade_gate_pending"
    if object_type == "ship" and summary["ship_model_available"]:
        return "bundle_available_gate_passed"
    return "physics_fallback"


def _predict_point(track: dict, horizon_s: int) -> dict:
    lat = float(track.get("lat", 0.0) or 0.0)
    lon = float(track.get("lon", 0.0) or 0.0)
    speed = float(track.get("speed", 0.0) or 0.0)
    heading = float(track.get("heading", 0.0) or 0.0)
    distance_m = speed * horizon_s
    next_lat, next_lon = destination_point(lat, lon, heading, distance_m)
    return {
        "horizon_s": int(horizon_s),
        "lat": round(next_lat, 7),
        "lon": round(next_lon, 7),
        "alt": float(track.get("alt", 0.0) or 0.0),
        "uncertainty_radius_m": round(max(50.0, distance_m * 0.08), 3),
        "prediction_confidence": round(_clamp(float(track.get("confidence", 0.75)) - horizon_s * 0.001, 0.1, 0.99), 4),
    }


def _track_from_detection(detection: dict) -> dict:
    track_id = str(detection.get("track_id") or f"trk-{detection.get('detection_id', 'unknown')}")
    point = _history_point(detection)
    return {
        "track_id": track_id,
        "object_type": _normalize_object_type(detection.get("object_type")),
        "lat": float(detection.get("lat", 0.0) or 0.0),
        "lon": float(detection.get("lon", 0.0) or 0.0),
        "alt": float(detection.get("alt", 0.0) or 0.0),
        "speed": float(detection.get("speed", 0.0) or 0.0),
        "heading": float(detection.get("heading", 0.0) or 0.0),
        "confidence": _clamp(float(detection.get("confidence", 0.75)), 0.0, 1.0),
        "track_quality": _clamp(float(detection.get("confidence", 0.75)), 0.0, 1.0),
        "last_update_time": float(detection.get("timestamp", 0.0) or 0.0),
        "history_path": [point],
        "metadata": {"source": "track_state_updater"},
    }


def _update_track(track: dict, detection: dict) -> dict:
    track.update(
        {
            "lat": float(detection.get("lat", track.get("lat", 0.0)) or 0.0),
            "lon": float(detection.get("lon", track.get("lon", 0.0)) or 0.0),
            "alt": float(detection.get("alt", track.get("alt", 0.0)) or 0.0),
            "speed": float(detection.get("speed", track.get("speed", 0.0)) or 0.0),
            "heading": float(detection.get("heading", track.get("heading", 0.0)) or 0.0),
            "confidence": _clamp(float(detection.get("confidence", track.get("confidence", 0.75))), 0.0, 1.0),
            "last_update_time": float(detection.get("timestamp", track.get("last_update_time", 0.0)) or 0.0),
        }
    )
    history = list(track.get("history_path") or [])
    history.append(_history_point(detection))
    track["history_path"] = history[-50:]
    track["track_quality"] = round((float(track.get("track_quality", 0.75)) + float(track["confidence"])) / 2.0, 4)
    return track


def _match_track(detection: dict, tracks: Sequence[dict]) -> dict | None:
    detection_id = str(detection.get("detection_id") or "")
    for track in tracks:
        if detection_id and str(track.get("track_id", "")).endswith(detection_id):
            return track
    if not tracks:
        return None
    best = min(
        tracks,
        key=lambda item: haversine_m(
            float(detection.get("lat", 0.0)),
            float(detection.get("lon", 0.0)),
            float(item.get("lat", 0.0)),
            float(item.get("lon", 0.0)),
        ),
    )
    distance = haversine_m(float(detection.get("lat", 0.0)), float(detection.get("lon", 0.0)), float(best.get("lat", 0.0)), float(best.get("lon", 0.0)))
    return best if distance <= 5_000.0 else None


def _history_point(item: dict) -> dict:
    return {
        "timestamp": float(item.get("timestamp", 0.0) or 0.0),
        "lat": float(item.get("lat", 0.0) or 0.0),
        "lon": float(item.get("lon", 0.0) or 0.0),
        "alt": float(item.get("alt", 0.0) or 0.0),
        "speed": float(item.get("speed", 0.0) or 0.0),
        "heading": float(item.get("heading", 0.0) or 0.0),
        "confidence": _clamp(float(item.get("confidence", 0.75)), 0.0, 1.0),
    }


def _connected_groups(tracks: Sequence[dict], relations: Sequence[dict]) -> List[dict]:
    ids = [str(track.get("track_id") or track.get("detection_id")) for track in tracks]
    adjacency = {track_id: set() for track_id in ids}
    for relation in relations:
        left = relation["source_track_id"]
        right = relation["target_track_id"]
        adjacency.setdefault(left, set()).add(right)
        adjacency.setdefault(right, set()).add(left)
    seen = set()
    groups = []
    for track_id in ids:
        if track_id in seen:
            continue
        stack = [track_id]
        component = []
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            component.append(current)
            stack.extend(sorted(adjacency.get(current, set()) - seen))
        if len(component) >= 2:
            member_tracks = [track for track in tracks if str(track.get("track_id") or track.get("detection_id")) in component]
            groups.append(_build_group(len(groups) + 1, member_tracks, component))
    return groups


def _build_group(index: int, tracks: Sequence[dict], member_ids: Sequence[str]) -> dict:
    lats = [float(track.get("lat", 0.0)) for track in tracks]
    lons = [float(track.get("lon", 0.0)) for track in tracks]
    types = {_normalize_object_type(track.get("object_type")) for track in tracks}
    group_type = "air_formation" if types == {"aircraft"} else "surface_group" if types == {"ship"} else "mixed_group"
    return {
        "group_id": f"group-{index:03d}",
        "group_type": group_type,
        "member_track_ids": sorted(member_ids),
        "centroid": {"lat": round(sum(lats) / len(lats), 7), "lon": round(sum(lons) / len(lons), 7)},
        "cohesion_score": 0.82,
        "evidence": ["成员目标距离、航向和速度相近，构成同一关系连通分量。"],
    }


def _type_scores(object_type: str, confidence: float, speed: float, alt: float) -> Dict[str, float]:
    scores = {value: 0.05 for value in OBJECT_TYPES}
    scores[object_type] = max(confidence, 0.5)
    if alt > 1000 and speed > 80:
        scores["aircraft"] = max(scores["aircraft"], 0.82)
    if alt < 100 and 1 <= speed <= 25:
        scores["ship"] = max(scores["ship"], 0.78)
    if alt > 50 and speed < 90:
        scores["uav"] = max(scores["uav"], 0.62)
    total = sum(scores.values())
    return {key: round(value / total, 4) for key, value in scores.items()}


def _normalize_object_type(value: Any) -> str:
    normalized = str(value or "unknown").lower()
    return normalized if normalized in OBJECT_TYPES else "unknown"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def heading_difference(left: float, right: float) -> float:
    return abs((left - right + 180.0) % 360.0 - 180.0)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_M * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def lat_lon_to_offset(anchor_lat: float, anchor_lon: float, lat: float, lon: float) -> tuple[float, float]:
    north = math.radians(lat - anchor_lat) * EARTH_RADIUS_M
    east = math.radians(lon - anchor_lon) * EARTH_RADIUS_M * math.cos(math.radians(anchor_lat))
    return east, north


def offset_to_lat_lon(anchor_lat: float, anchor_lon: float, east_m: float, north_m: float) -> tuple[float, float]:
    lat = anchor_lat + math.degrees(north_m / EARTH_RADIUS_M)
    lon = anchor_lon + math.degrees(east_m / (EARTH_RADIUS_M * max(math.cos(math.radians(anchor_lat)), 1e-6)))
    return lat, lon


def destination_point(lat: float, lon: float, heading_deg: float, distance_m: float) -> tuple[float, float]:
    bearing = math.radians(heading_deg)
    phi1 = math.radians(lat)
    lambda1 = math.radians(lon)
    angular = distance_m / EARTH_RADIUS_M
    phi2 = math.asin(math.sin(phi1) * math.cos(angular) + math.cos(phi1) * math.sin(angular) * math.cos(bearing))
    lambda2 = lambda1 + math.atan2(
        math.sin(bearing) * math.sin(angular) * math.cos(phi1),
        math.cos(angular) - math.sin(phi1) * math.sin(phi2),
    )
    return math.degrees(phi2), math.degrees(lambda2)
