from __future__ import annotations

import copy
import hashlib
import math
import threading
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from commander_gateway.clients import AmosClient, CommanderClient
from commander_gateway.config import GatewayConfig
from commander_gateway.errors import GatewayError, UpstreamError
from commander_gateway.schemas import (
    PACKAGE_SCHEMA_VERSION,
    AmosEventV1,
    AmosSnapshotV1,
    CommanderProjectionV1,
    IdempotencyRecordV1,
    MediaRefV1,
    StoredWorkflowRecordV1,
    WorkflowSubmitV1,
)
from commander_gateway.store import FileGatewayStore, canonical_json_bytes


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _absolute_media_uris(value: Any, amos_base_url: str) -> Any:
    detached = copy.deepcopy(value)

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if key == "uri" and isinstance(child, str) and child.startswith("/static/"):
                    item[key] = f"{amos_base_url}{child}"
                else:
                    visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(detached)
    return detached


def _track_speed_kts(track: dict[str, Any]) -> float | None:
    points = [
        point
        for point in track.get("history_path") or []
        if isinstance(point, dict)
        and point.get("sim_time") is not None
        and point.get("lat") is not None
        and point.get("lng", point.get("lon")) is not None
    ]
    if len(points) < 2:
        return None
    end = points[-1]
    start = next(
        (
            point
            for point in reversed(points[:-1])
            if float(point["sim_time"]) < float(end["sim_time"])
        ),
        None,
    )
    if start is None:
        return None
    elapsed_seconds = float(end["sim_time"]) - float(start["sim_time"])
    if elapsed_seconds <= 0:
        return None
    lat1, lat2 = math.radians(float(start["lat"])), math.radians(float(end["lat"]))
    delta_lat = lat2 - lat1
    delta_lon = math.radians(
        float(end.get("lng", end.get("lon")))
        - float(start.get("lng", start.get("lon")))
    )
    haversine = (
        math.sin(delta_lat / 2.0) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2.0) ** 2
    )
    distance_nm = (2.0 * 6_371_000.0 * math.asin(min(1.0, math.sqrt(haversine)))) / 1852.0
    return round(distance_nm / (elapsed_seconds / 3600.0), 3)


def _gateway_mission_input(snapshot: dict, chain: dict) -> dict[str, Any]:
    """Build Commander input only from the Gateway-verified AMOS package."""
    scenario_id = str(snapshot.get("scenario_id") or chain.get("scenario_id") or "")
    tracks = [item for item in snapshot.get("tracks") or [] if isinstance(item, dict)]
    assets = [item for item in snapshot.get("assets") or [] if isinstance(item, dict)]
    stage_transfer = (
        copy.deepcopy(chain.get("submission_context"))
        if isinstance(chain.get("submission_context"), dict)
        else {}
    )

    contacts = []
    for track in tracks:
        track_id = str(track.get("track_id") or "").strip()
        lat, lon = track.get("lat"), track.get("lon")
        if not track_id or lat is None or lon is None:
            continue
        geo = {"lat": float(lat), "lon": float(lon)}
        domain = str(track.get("domain_hint") or "unknown").lower()
        speed_kts = _track_speed_kts(track)
        sensor_sources = list(track.get("sensor_sources") or [])
        ais_observed = any(str(source).casefold() == "ais" for source in sensor_sources)
        prior_classification = str(track.get("classification") or "unknown").lower()
        kind = "surface-contact" if domain in {"maritime", "surface", "sea"} else f"{domain}-contact"
        contacts.append(
            {
                "contact_id": track_id,
                "track_id": track_id,
                "kind": kind,
                "location": f"{geo['lat']},{geo['lon']}",
                "geo": geo,
                "velocity": copy.deepcopy(track.get("velocity")),
                "speed_kts": speed_kts,
                "intent": None,
                "classification": prior_classification,
                "affiliation": "unknown",
                "metadata": {
                    "amos_track_id": track_id,
                    "geo": geo,
                    "domain_hint": track.get("domain_hint"),
                    "classification": prior_classification,
                    "prior_classification": prior_classification,
                    "affiliation": "unknown",
                    "confidence": track.get("confidence"),
                    "heading_deg": track.get("heading_deg"),
                    "speed_kts": speed_kts,
                    "ais_observed": ais_observed,
                    "uncertainty": copy.deepcopy(track.get("uncertainty") or {}),
                    "source_observation_ids": list(track.get("source_observation_ids") or []),
                    "sensor_sources": sensor_sources,
                },
            }
        )

    friendly_platforms = []
    for asset in assets:
        asset_id = str(asset.get("asset_id") or "").strip()
        if not asset_id:
            continue
        position = copy.deepcopy(asset.get("position") or {})
        energy = asset.get("battery_pct")
        if energy is None:
            energy = asset.get("fuel_pct")
        energy_ratio = max(0.0, min(1.0, float(energy if energy is not None else 100) / 100.0))
        comms_ratio = max(
            0.0,
            min(1.0, float(asset.get("comms_strength") if asset.get("comms_strength") is not None else 100) / 100.0),
        )
        active = str(asset.get("status") or "").casefold() in {"active", "operational", "holding"}
        friendly_platforms.append(
            {
                "platform_id": asset_id,
                "platform_type": asset.get("asset_type") or asset.get("domain") or "generic",
                "readiness": round((energy_ratio * 0.55 + comms_ratio * 0.45) if active else 0.0, 3),
                "munitions": 0,
                "location": f"{position.get('lat')},{position.get('lon')}",
                "metadata": {
                    "position": position,
                    "domain": asset.get("domain"),
                    "status": asset.get("status"),
                    "heading_deg": asset.get("heading_deg"),
                    "speed_kts": asset.get("speed_kts"),
                    "sensors": list(asset.get("sensors") or []),
                    "battery_pct": asset.get("battery_pct"),
                    "fuel_pct": asset.get("fuel_pct"),
                    "comms_strength": asset.get("comms_strength"),
                },
            }
        )

    detections_by_time: dict[float, list[dict[str, Any]]] = {}
    for track in tracks:
        track_id = str(track.get("track_id") or "").strip()
        if not track_id:
            continue
        domain = str(track.get("domain_hint") or "unknown").lower()
        object_type = "ship" if domain in {"maritime", "surface", "sea"} else (
            "aircraft" if domain in {"air", "aviation"} else "unknown"
        )
        speed_kts = _track_speed_kts(track)
        speed_mps = float(speed_kts or 0.0) * 0.514444
        for index, point in enumerate(track.get("history_path") or []):
            if not isinstance(point, dict):
                continue
            lat, lon = point.get("lat"), point.get("lng", point.get("lon"))
            if lat is None or lon is None:
                continue
            timestamp = float(point.get("sim_time", 0.0) or 0.0)
            detections_by_time.setdefault(timestamp, []).append(
                {
                    "detection_id": f"{track_id}-t{int(round(timestamp * 1000)):09d}",
                    "object_type": object_type,
                    "timestamp": timestamp,
                    "lat": float(lat),
                    "lon": float(lon),
                    "alt": float(point.get("alt_m", point.get("alt", 0.0)) or 0.0),
                    "speed": speed_mps,
                    "heading": float(point.get("heading", track.get("heading_deg", 0.0)) or 0.0),
                    "confidence": float(point.get("confidence", track.get("confidence", 0.5)) or 0.5),
                    "source_agent": "amos_sensor_fusion",
                    "metadata": {
                        "amos_track_id": track_id,
                        "source_contact_id": track_id,
                        "domain_hint": track.get("domain_hint"),
                        "speed_kts": speed_kts,
                        "ais_observed": any(
                            str(source).casefold() == "ais"
                            for source in track.get("sensor_sources") or []
                        ),
                        "sensor_sources": list(track.get("sensor_sources") or []),
                        "sample_index": index,
                        "source_observation_ids": list(point.get("source_observation_ids") or []),
                        "source_media_ids": list(point.get("source_media_ids") or []),
                        "source_capture_ids": list(point.get("source_capture_ids") or []),
                    },
                }
            )
    frame_times = sorted(detections_by_time)[-10:]
    scene = {"scenario_id": scenario_id, "protected_assets": []}
    perception_frames = [
        {
            "task_id": f"{scenario_id}-frame-{int(round(timestamp * 1000)):09d}",
            "message_type": "perception_result",
            "algorithm_level": "medium",
            "scene": copy.deepcopy(scene),
            "detections": detections_by_time[timestamp],
        }
        for timestamp in frame_times
    ]

    objective = str(chain.get("objective") or "基于当前仿真快照完成态势分析")
    return {
        "scenario_id": scenario_id,
        "scenario_name": scenario_id,
        "mission_type": "multimodal_tracking_and_assessment",
        "objective": objective,
        "intelligence_text": (
            f"AMOS verified snapshot at {snapshot.get('sim_time_ms', 0)} ms: "
            f"{len(contacts)} contacts and {len(friendly_platforms)} friendly platforms."
        ),
        "contacts": contacts,
        "friendly_platforms": friendly_platforms,
        "protected_assets": [],
        "perception_frames": perception_frames,
        "observations": copy.deepcopy(snapshot.get("observations") or []),
        "evidence": copy.deepcopy(stage_transfer.get("evidence_items") or []),
        "stage_transfer": stage_transfer,
        "constraints": {
            "no_real_execution": True,
            "current_time_only": True,
            "do_not_infer_future_events": True,
        },
        "scene": scene,
        "environment": {"network": copy.deepcopy(snapshot.get("network") or {})},
        "simulation_time_sec": float(snapshot.get("sim_time_ms", 0) or 0) / 1000.0,
        "simulation_mode": "safe",
        "metadata": {
            "run_id": snapshot.get("run_id"),
            "snapshot_sequence": snapshot.get("sequence"),
            "causal_cutoff_sec": float(snapshot.get("sim_time_ms", 0) or 0) / 1000.0,
            "source_system": "AMOS",
            "source_contract": "amos.commander.gateway-derived.v1",
            "director_checkpoint_id": stage_transfer.get("checkpoint_id"),
            "scenario_branch": stage_transfer.get("branch"),
        },
    }


_SENSITIVE_KEYS = {
    "truth_id",
    "associated_threat_id",
    "target_threat_id",
    "is_hostile",
    "side",
}
_SENSITIVE_PREFIXES = ("agent", "a2a", "commander")


class GatewayService:
    def __init__(
        self,
        config: GatewayConfig,
        *,
        store: FileGatewayStore | None = None,
        amos_client: Any | None = None,
        commander_client: Any | None = None,
    ) -> None:
        self.config = config
        self.store = store or FileGatewayStore(config.state_dir)
        self.amos = amos_client or AmosClient(config)
        self.commander = commander_client or CommanderClient(config)
        self._submit_lock = threading.RLock()

    @staticmethod
    def _validate_provenance(payload: dict, label: str) -> None:
        provenance = payload.get("provenance")
        if not isinstance(provenance, dict) or provenance.get("simulated") is not True:
            raise GatewayError(
                "INVALID_AMOS_CONTRACT",
                f"{label} must declare simulated=true",
                422,
                False,
            )

    @staticmethod
    def _validate_nested_media_refs(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "media_refs":
                    if not isinstance(child, list):
                        raise GatewayError(
                            "INVALID_AMOS_CONTRACT", "media_refs must be a list", 422
                        )
                    try:
                        for media_ref in child:
                            MediaRefV1.model_validate(media_ref)
                    except ValidationError as exc:
                        raise GatewayError(
                            "INVALID_AMOS_CONTRACT",
                            "AMOS media reference is invalid",
                            422,
                        ) from exc
                else:
                    GatewayService._validate_nested_media_refs(child)
        elif isinstance(value, list):
            for child in value:
                GatewayService._validate_nested_media_refs(child)

    @staticmethod
    def _reject_sensitive_keys(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = str(key).casefold()
                if normalized in _SENSITIVE_KEYS or normalized.startswith(
                    _SENSITIVE_PREFIXES
                ):
                    raise GatewayError(
                        "INVALID_AMOS_CONTRACT",
                        "AMOS payload contains a sensitive field",
                        422,
                    )
                GatewayService._reject_sensitive_keys(child)
        elif isinstance(value, list):
            for child in value:
                GatewayService._reject_sensitive_keys(child)

    def _read_time_slice(self, request: WorkflowSubmitV1) -> tuple[dict, list, dict]:
        snapshot = self.amos.get_snapshot()
        events = self.amos.get_events(after_sequence=0)
        if not isinstance(snapshot, dict):
            raise GatewayError("INVALID_AMOS_CONTRACT", "AMOS snapshot is invalid", 422)
        try:
            AmosSnapshotV1.model_validate(snapshot)
        except ValidationError as exc:
            raise GatewayError(
                "INVALID_AMOS_CONTRACT", "AMOS snapshot contract is invalid", 422
            ) from exc
        self._validate_nested_media_refs(snapshot)
        self._reject_sensitive_keys(snapshot)
        try:
            for recent_event in snapshot["recent_events"]:
                AmosEventV1.model_validate(recent_event)
        except ValidationError as exc:
            raise GatewayError(
                "INVALID_AMOS_CONTRACT",
                "AMOS snapshot recent event is invalid",
                422,
            ) from exc
        for recent_event in snapshot["recent_events"]:
            if recent_event["run_id"] != request.run_id:
                raise GatewayError(
                    "RUN_ID_MISMATCH",
                    "snapshot recent event run does not match request",
                    409,
                )
            if recent_event["sequence"] > snapshot["sequence"]:
                raise GatewayError(
                    "EVENT_SEQUENCE_INVALID",
                    "snapshot recent event exceeds the snapshot cursor",
                    422,
                )
        if snapshot.get("schema_version") != "amos.simulation.snapshot.v1":
            raise GatewayError(
                "INVALID_AMOS_CONTRACT", "unsupported AMOS snapshot schema", 422
            )
        self._validate_provenance(snapshot, "snapshot")
        if snapshot.get("run_id") != request.run_id:
            raise GatewayError(
                "RUN_ID_MISMATCH", "requested run does not match AMOS snapshot", 409
            )
        sequence = snapshot.get("sequence")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
            raise GatewayError(
                "INVALID_AMOS_CONTRACT", "snapshot sequence is invalid", 422
            )
        chains = snapshot.get("simulation_chains")
        if not isinstance(chains, list):
            raise GatewayError(
                "INVALID_AMOS_CONTRACT", "snapshot simulation_chains is invalid", 422
            )
        chain = next(
            (
                item
                for item in chains
                if isinstance(item, dict) and item.get("chain_id") == request.chain_id
            ),
            None,
        )
        if chain is None:
            raise GatewayError(
                "CHAIN_NOT_FOUND", "target chain does not exist in AMOS snapshot", 404
            )
        if not isinstance(events, list):
            raise GatewayError("INVALID_AMOS_CONTRACT", "AMOS events are invalid", 422)

        sliced_events = []
        for event in events:
            if not isinstance(event, dict):
                raise GatewayError("INVALID_AMOS_CONTRACT", "AMOS event is invalid", 422)
            try:
                AmosEventV1.model_validate(event)
            except ValidationError as exc:
                raise GatewayError(
                    "INVALID_AMOS_CONTRACT", "AMOS event contract is invalid", 422
                ) from exc
            self._reject_sensitive_keys(event)
            if event.get("schema_version") != "amos.simulation.event.v1":
                raise GatewayError(
                    "INVALID_AMOS_CONTRACT", "unsupported AMOS event schema", 422
                )
            self._validate_provenance(event, "event")
            if event.get("run_id") != request.run_id:
                raise GatewayError(
                    "RUN_ID_MISMATCH", "AMOS event run does not match request", 409
                )
            event_sequence = event.get("sequence")
            if (
                not isinstance(event_sequence, int)
                or isinstance(event_sequence, bool)
                or event_sequence < 1
            ):
                raise GatewayError(
                    "EVENT_SEQUENCE_INVALID", "AMOS event sequence is invalid", 422
                )
            if event_sequence <= sequence:
                sliced_events.append(event)

        actual_sequences = [event["sequence"] for event in sliced_events]
        if actual_sequences != list(range(1, sequence + 1)):
            raise GatewayError(
                "EVENT_SEQUENCE_INVALID",
                "AMOS events are not continuous through the snapshot cursor",
                422,
            )
        return (
            _absolute_media_uris(snapshot, self.config.amos_base_url),
            _absolute_media_uris(sliced_events, self.config.amos_base_url),
            copy.deepcopy(chain),
        )

    @staticmethod
    def _projection(record: dict) -> CommanderProjectionV1:
        try:
            return CommanderProjectionV1.model_validate(record["projection"])
        except (KeyError, ValidationError, TypeError) as exc:
            raise GatewayError(
                "WORKFLOW_CORRUPT", "Gateway workflow record is corrupt", 500
            ) from exc

    @staticmethod
    def _workflow_record(record: dict) -> dict:
        try:
            return StoredWorkflowRecordV1.model_validate(record).model_dump(mode="json")
        except (ValidationError, TypeError) as exc:
            raise GatewayError(
                "WORKFLOW_CORRUPT", "Gateway workflow record is corrupt", 500
            ) from exc

    @staticmethod
    def _idempotency_record(record: dict) -> dict:
        try:
            return IdempotencyRecordV1.model_validate(record).model_dump(mode="json")
        except (ValidationError, TypeError) as exc:
            raise GatewayError(
                "IDEMPOTENCY_CORRUPT", "idempotency record is corrupt", 500
            ) from exc

    def _read_workflow(self, workflow_id: str) -> dict:
        return self._workflow_record(self.store.read_workflow(workflow_id))

    def _persist(
        self,
        workflow_id: str,
        digest: str,
        request_key: str,
        projection: CommanderProjectionV1,
        request: WorkflowSubmitV1,
        commander_payload: dict,
    ) -> None:
        workflow_record = {
            "digest": digest,
            "request_key": request_key,
            "projection": projection.model_dump(mode="json"),
            "request": request.model_dump(mode="json"),
            "commander_payload": commander_payload,
        }
        self.store.save_workflow(workflow_id, workflow_record)
        self.store.save_idempotency(
            digest,
            {"workflow_id": workflow_id, "request_key": request_key},
        )

    def _submit_or_recover(self, workflow_id: str, payload: dict) -> dict:
        try:
            return self.commander.submit_workflow(payload)
        except UpstreamError as exc:
            if exc.status_code not in {409, 503, 504}:
                raise
            try:
                return self.commander.get_workflow(workflow_id)
            except UpstreamError:
                raise exc

    def _finish_submission(self, workflow_id: str, upstream: dict) -> CommanderProjectionV1:
        record = self._read_workflow(workflow_id)
        projection = self._projection(record)
        projection = projection.model_copy(
            update={
                "status": str(upstream.get("status", "queued")),
                "updated_at": str(upstream.get("updated_at") or utc_now()),
                "last_error": upstream.get("last_error"),
            }
        )
        record["projection"] = projection.model_dump(mode="json")
        self.store.save_workflow(workflow_id, record)
        return projection

    def _recover_pending(self, workflow_id: str, record: dict) -> CommanderProjectionV1:
        try:
            upstream = self.commander.get_workflow(workflow_id)
        except UpstreamError as exc:
            if exc.status_code != 404:
                raise
            upstream = self._submit_or_recover(
                workflow_id, copy.deepcopy(record["commander_payload"])
            )
        return self._finish_submission(workflow_id, upstream)

    def submit(self, request: WorkflowSubmitV1) -> CommanderProjectionV1:
        with self._submit_lock:
            return self._submit_locked(request)

    def _submit_locked(self, request: WorkflowSubmitV1) -> CommanderProjectionV1:
        snapshot, events, chain = self._read_time_slice(request)
        event_cursor = snapshot["sequence"]
        parameters = request.commander_parameters()
        source_fingerprint = _digest(
            {
                "run_id": request.run_id,
                "chain_id": request.chain_id,
                "snapshot": snapshot,
                "events": events,
            }
        )
        request_key = _digest(
            {
                "run_id": request.run_id,
                "chain_id": request.chain_id,
                "snapshot_sequence": event_cursor,
                "source_fingerprint": source_fingerprint,
                "workflow_parameters": parameters,
            }
        )
        existing = self.store.find_idempotency_by_request_key(request_key)
        if existing is not None:
            _, entry = existing
            entry = self._idempotency_record(entry)
            workflow_id = entry["workflow_id"]
            record = self._read_workflow(workflow_id)
            projection = self._projection(record)
            if projection.status == "submitting":
                return self._recover_pending(workflow_id, record)
            return projection
        orphaned = self.store.find_workflow_by_request_key(request_key)
        if orphaned is not None:
            workflow_id, record = orphaned
            record = self._workflow_record(record)
            self.store.save_idempotency(
                record["digest"],
                {"workflow_id": workflow_id, "request_key": request_key},
            )
            projection = self._projection(record)
            if projection.status == "submitting":
                return self._recover_pending(workflow_id, record)
            return projection

        created_at = utc_now()
        package = {
            "schema_version": PACKAGE_SCHEMA_VERSION,
            "package_id": None,
            "created_at": created_at,
            "run_id": request.run_id,
            "chain_id": request.chain_id,
            "chain": chain,
            "snapshot": snapshot,
            "events": events,
            "source": {
                "system": "AMOS",
                "base_url": self.config.amos_base_url,
                "snapshot_sequence": event_cursor,
            },
        }
        # package_id is kept outside the bytes so the checksum remains a pure content hash.
        package.pop("package_id")
        package_id, package_checksum, body = self.store.save_package(package)
        digest = _digest(
            {
                "run_id": request.run_id,
                "chain_id": request.chain_id,
                "snapshot_sequence": event_cursor,
                "package_checksum": package_checksum,
                "workflow_parameters": parameters,
            }
        )
        workflow_id = f"amos-{digest[:24]}"
        attachment = {
            "id": package_id,
            "kind": "amos_simulation_package",
            "uri": (
                f"{self.config.public_base_url}/gateway/v1/packages/{package_id}"
            ),
            "mime_type": "application/json",
            "size_bytes": len(body),
            "checksum": {"algorithm": "sha256", "value": package_checksum},
            "name": f"amos-{request.run_id}-{request.chain_id}.json",
            "meta": {
                "schema_version": PACKAGE_SCHEMA_VERSION,
                "run_id": request.run_id,
                "chain_id": request.chain_id,
                "event_cursor": event_cursor,
            },
        }
        commander_payload = {
            **parameters,
            "workflow_id": workflow_id,
            "initial_context": {
                "mission_input": _gateway_mission_input(snapshot, chain),
            },
            "attachments": [attachment],
        }
        pending_projection = CommanderProjectionV1(
            workflow_id=workflow_id,
            run_id=request.run_id,
            chain_id=request.chain_id,
            package_id=package_id,
            package_checksum=package_checksum,
            event_cursor=event_cursor,
            status="submitting",
            work_list=[],
            trace=[],
            submitted_at=created_at,
            updated_at=created_at,
            last_error=None,
        )
        self._persist(
            workflow_id,
            digest,
            request_key,
            pending_projection,
            request,
            commander_payload,
        )
        upstream = self._submit_or_recover(workflow_id, commander_payload)
        return self._finish_submission(workflow_id, upstream)

    @staticmethod
    def _checkpoint_list(payload: Any, field: str) -> list:
        if isinstance(payload, dict):
            value = payload.get(field, [])
        else:
            value = payload
        return value if isinstance(value, list) else []

    @staticmethod
    def _resolved_workflow_result(checkpoint: Any) -> dict[str, Any]:
        if not isinstance(checkpoint, dict):
            return {}
        context = checkpoint.get("context")
        if not isinstance(context, dict):
            return {}
        raw_result = context.get("workflow_result")
        if not isinstance(raw_result, dict):
            return {}

        result = copy.deepcopy(raw_result)
        output_names = (
            "tracking_result",
            "threat_assessment_result",
            "cognition_result",
            "task_scheduling_result",
            "decision_planning_result",
            "compliance_authorization_result",
            "execution_simulation_result",
            "effect_evaluation_result",
        )
        outputs = {
            name: copy.deepcopy(context[name])
            for name in output_names
            if name in context
        }
        result["outputs"] = outputs

        resolved_activities = []
        for raw_activity in result.get("activity_results") or []:
            if not isinstance(raw_activity, dict):
                continue
            activity = copy.deepcopy(raw_activity)
            output_ref = str(activity.get("output_ref") or "")
            output_name = output_ref.removeprefix("outputs.")
            value = outputs.get(output_name)
            if isinstance(value, list):
                activity_id = str(activity.get("activity_id") or "")
                matching = next(
                    (
                        item
                        for item in value
                        if isinstance(item, dict)
                        and str(item.get("activity_id") or "") == activity_id
                    ),
                    None,
                )
                if isinstance(matching, dict):
                    value = matching.get("value", matching.get("output", matching))
            if isinstance(value, dict):
                activity["output"] = copy.deepcopy(value)
            resolved_activities.append(activity)
        result["activity_results"] = resolved_activities
        return result

    def get_projection(self, workflow_id: str) -> CommanderProjectionV1:
        record = self._read_workflow(workflow_id)
        upstream = self.commander.get_workflow(workflow_id)
        try:
            work_list = self._checkpoint_list(
                self.commander.get_work_list(workflow_id), "work_list"
            )
        except UpstreamError as exc:
            if exc.status_code != 404:
                raise
            work_list = []
        try:
            trace = self._checkpoint_list(self.commander.get_trace(workflow_id), "trace")
        except UpstreamError as exc:
            if exc.status_code != 404:
                raise
            trace = []

        result: dict[str, Any] = {}
        if str(upstream.get("status") or "").lower() == "completed":
            try:
                result = self._resolved_workflow_result(
                    self.commander.get_checkpoint(workflow_id)
                )
            except UpstreamError as exc:
                if exc.status_code != 404:
                    raise

        projection = self._projection(record)
        updated = projection.model_copy(
            update={
                "status": str(upstream.get("status", projection.status)),
                "work_list": work_list,
                "trace": trace,
                "result": result,
                "updated_at": str(upstream.get("updated_at") or utc_now()),
                "last_error": upstream.get("last_error"),
            }
        )
        record["projection"] = updated.model_dump(mode="json")
        self.store.save_workflow(workflow_id, record)
        return updated

    def get_work_list(self, workflow_id: str) -> list:
        return self._get_checkpoint_field(workflow_id, "work_list")

    def get_trace(self, workflow_id: str) -> list:
        return self._get_checkpoint_field(workflow_id, "trace")

    def _get_checkpoint_field(self, workflow_id: str, field: str) -> list:
        record = self._read_workflow(workflow_id)
        upstream = self.commander.get_workflow(workflow_id)
        call = (
            self.commander.get_work_list
            if field == "work_list"
            else self.commander.get_trace
        )
        try:
            value = self._checkpoint_list(call(workflow_id), field)
        except UpstreamError as exc:
            if exc.status_code != 404:
                raise
            value = []
        current_projection = self._projection(record)
        projection = current_projection.model_copy(
            update={
                "status": str(upstream.get("status") or current_projection.status),
                field: value,
                "updated_at": str(upstream.get("updated_at") or utc_now()),
                "last_error": upstream.get("last_error"),
            }
        )
        record["projection"] = projection.model_dump(mode="json")
        self.store.save_workflow(workflow_id, record)
        return value

    def resume(self, workflow_id: str) -> CommanderProjectionV1:
        record = self._read_workflow(workflow_id)
        payload = copy.deepcopy(record["commander_payload"])
        payload.pop("workflow_id", None)
        projection = self._projection(record).model_copy(
            update={"status": "resuming", "updated_at": utc_now()}
        )
        record["projection"] = projection.model_dump(mode="json")
        self.store.save_workflow(workflow_id, record)
        try:
            upstream = self.commander.resume_workflow(workflow_id, payload)
        except UpstreamError as exc:
            if exc.status_code not in {409, 503, 504}:
                raise
            try:
                upstream = self.commander.get_workflow(workflow_id)
            except UpstreamError:
                raise exc
        self._finish_submission(workflow_id, upstream)
        return self.get_projection(workflow_id)

    def health(self) -> tuple[int, dict]:
        dependencies: dict[str, dict] = {}
        healthy = True
        for name, call in (
            ("amos", self.amos.get_status),
            ("commander", self.commander.health),
        ):
            try:
                details = call()
                dependencies[name] = {"status": "ok", "details": details}
            except GatewayError as exc:
                healthy = False
                dependencies[name] = {
                    "status": "unavailable",
                    "error": exc.detail(),
                }
            except Exception as exc:  # health must report both dependencies
                healthy = False
                dependencies[name] = {
                    "status": "unavailable",
                    "error": {
                        "code": f"{name.upper()}_UNAVAILABLE",
                        "message": str(exc),
                        "retriable": True,
                    },
                }
        status = "ok" if healthy else "degraded"
        return (200 if healthy else 503), {
            "status": status,
            "gateway": {"status": "ok", "single_worker_only": True},
            "dependencies": dependencies,
        }
