import json
import os
import time
from copy import deepcopy
from typing import Dict, Iterable, Tuple

from agent.algorithm_library.client import AlgorithmLibraryClient, AlgorithmLibraryError
from a2a_protocol.messages import build_task_response
from decision_agents.common.a2a_payloads import agent_response_to_a2a_response, run_agent_payload
from decision_agents.compliance_authorization.agent import ComplianceAuthorizationAgent
from decision_agents.decision_planning.agent import DecisionPlanningAgent
from protocol_contracts import validate_task_payload, validate_task_response
from skill_catalog import skill_contract


class LocalAgentRuntime:
    """
    Local in-process runtime for Commander workflow debugging.

    It mirrors the A2A discovery/auth/send flow without Nacos, HTTP, or uvicorn.
    This is useful when you want to validate workflow branching locally before
    starting the full distributed stack.
    """

    def __init__(self):
        self._task_response_cache = {}
        self._stream_response_cache = {}
        self._workflow_work_lists = {}
        self._algorithm_agents = {
            "decision_planning": DecisionPlanningAgent(),
            "compliance_authorization": ComplianceAuthorizationAgent(),
        }

    AGENTS = {
        "recon": {
            "name": "Local_Recon_Agent",
            "description": "Local reconnaissance unit.",
            "role": "recon",
        },
        "execution_control": {
            "name": "Local_Execution_Control_Agent",
            "description": "Local execution control planner.",
            "role": "execution_control",
        },
        "artillery": {
            "name": "Local_Artillery_Agent",
            "description": "Local artillery simulation unit.",
            "role": "artillery",
        },
        "evaluator": {
            "name": "Local_Evaluator_Agent",
            "description": "Local strike evaluation unit.",
            "role": "evaluator",
        },
        "assault": {
            "name": "Local_Assault_Agent",
            "description": "Local assault unit.",
            "role": "assault",
        },
        "decision_planning": {
            "name": "Local_Decision_Planning_Agent",
            "description": "Local decision planning unit.",
            "role": "decision_planning",
        },
        "tactical_intelligence": {
            "name": "Local_Tactical_Intelligence_Agent",
            "description": "Local tactical intelligence unit.",
            "role": "tactical_intelligence",
        },
        "track_threat": {
            "name": "Local_Track_Threat_Agent",
            "description": "Local track and threat analysis unit.",
            "role": "track_threat",
        },
        "task_scheduling": {
            "name": "Local_Task_Scheduling_Agent",
            "description": "Local task scheduling and resource allocation unit.",
            "role": "task_scheduling",
        },
        "compliance_authorization": {
            "name": "Local_Compliance_Authorization_Agent",
            "description": "Local compliance and authorization unit.",
            "role": "compliance_authorization",
        },
        "simulation_execution": {
            "name": "Local_Simulation_Execution_Agent",
            "description": "Local execution simulation unit.",
            "role": "simulation_execution",
        },
        "closed_loop": {
            "name": "Local_Closed_Loop_Optimization_Agent",
            "description": "Local execution control, effect assessment and closed-loop optimization unit.",
            "role": "closed_loop",
        },
    }

    ALGOLIB_ROLE_PIPELINE = {
        "tactical_intelligence": [
            "battlefield_rtdetr_detector",
            "edl_evidential_verifier",
            "motr_neural_kalman_tracker",
            "multimodal_mamba_fusion",
            "supcon_meta_classifier",
        ],
        "trajectory_tracking": ["motr_neural_kalman_tracker"],
        "threat_ranking": ["supcon_meta_classifier"],
    }

    def discover(self, role: str) -> Dict[str, str]:
        if role not in self.AGENTS:
            raise ValueError(f"Unsupported local role: {role}")
        return dict(self.AGENTS[role])

    @staticmethod
    def _work_item_from_payload(payload: dict) -> str:
        return payload.get("work_item") or payload.get("task_id", "work-item-001")

    def _capture_work_list(self, payload: dict) -> None:
        workflow_id = payload.get("workflow_id")
        work_list = payload.get("work_list")
        if workflow_id and isinstance(work_list, list):
            self._workflow_work_lists[workflow_id] = deepcopy(work_list)

    def get_work_list(self, workflow_id: str) -> list[dict]:
        return deepcopy(self._workflow_work_lists.get(workflow_id, []))

    def authenticate(self, role: str) -> str:
        self.discover(role)
        return f"local-token-{role}"

    def send_message(self, role: str, payload: dict) -> dict:
        self.discover(role)
        skill_id = payload.get("required_skill") or payload.get("command")
        payload = validate_task_payload(payload, {"id": skill_id, **skill_contract(skill_id)})
        self._capture_work_list(payload)
        work_item = self._work_item_from_payload(payload)
        if work_item in self._task_response_cache:
            return self._task_response_cache[work_item]

        if role in self._algorithm_agents:
            agent = self._algorithm_agents[role]
            algorithm_response = run_agent_payload(agent, agent.agent_name, payload)
            response = agent_response_to_a2a_response(
                payload=payload,
                response=algorithm_response,
                agent_name=agent.agent_name,
                work_list_size=len(self.get_work_list(payload.get("workflow_id"))),
            )
            response["mode"] = "local"
            response["execution_mode"] = "local_agent"
            self._attach_decision_algorithm_calls(response, agent.agent_name, algorithm_response)
            self._task_response_cache[work_item] = response
            return response

        output, message = self._output_for(role, payload)
        response = build_task_response(
            workflow_id=payload.get("workflow_id"),
            work_item=work_item,
            agent=self.AGENTS[role]["name"],
            role=role,
            command=payload.get("command"),
            status="completed",
            output=output,
            metrics={"latency_ms": 0.0, "duration_ms": 0.0},
            message=message,
            work_list_size=len(self.get_work_list(payload.get("workflow_id"))),
            extra={"mode": "local", "execution_mode": "local_agent"},
        )
        validate_task_response(payload, response, {"id": skill_id, **skill_contract(skill_id)})
        self._task_response_cache[work_item] = response
        return response

    def send_message_stream(self, role: str, payload: dict) -> Iterable[dict]:
        self.discover(role)
        self._capture_work_list(payload)
        work_item = self._work_item_from_payload(payload)
        cached_events = self._stream_response_cache.get(work_item)
        if cached_events is not None:
            for event in cached_events:
                yield event
            return

        if role != "artillery":
            events = [{
                "status": "Completed",
                "progress": "100%",
                "message": self._message_for(role, payload),
            }]
            self._stream_response_cache[work_item] = events
            yield from events
            return

        events = [
            {"status": "Working", "progress": "10%", "message": "Target locked"},
            {"status": "Working", "progress": "30%", "message": "Firing Volley 1"},
            {"status": "Working", "progress": "60%", "message": "Impact confirmed. Adjusting aim."},
            {"status": "Completed", "progress": "100%", "message": "Target suppression complete"},
        ]
        self._stream_response_cache[work_item] = events
        for event in events:
            time.sleep(0.1)
            yield event

    @staticmethod
    def encode_stream_event(event: dict) -> str:
        return json.dumps(event, ensure_ascii=False)

    def execute(self, role: str, payload: dict, stream: bool = False) -> Tuple[dict, list]:
        card = self.discover(role)
        token = self.authenticate(role)
        events = []
        if stream:
            events = list(self.send_message_stream(role, payload))
            output, message = self._output_for(role, payload)
            response = build_task_response(
                workflow_id=payload.get("workflow_id"),
                work_item=self._work_item_from_payload(payload),
                agent=card["name"],
                role=role,
                command=payload.get("command"),
                status="completed" if events and events[-1].get("status") == "Completed" else "accepted",
                output=output,
                metrics={"stream_events": len(events), "duration_ms": 0.0},
                message=message,
                work_list_size=len(self.get_work_list(payload.get("workflow_id"))),
                extra={"mode": "local", "execution_mode": "local_agent", "token": token},
            )
        else:
            response = self.send_message(role, payload)
            response["token"] = token
        response["agent_card"] = card
        return response, events

    @staticmethod
    def _message_for(role: str, payload: dict) -> str:
        command = payload.get("command", "")
        if role == "recon":
            return f"Local recon completed command={command}"
        if role == "execution_control":
            return f"Local execution control completed command={command}"
        if role == "artillery":
            return f"Local artillery completed command={command}"
        if role == "evaluator":
            return f"Local evaluator completed command={command}"
        if role == "assault":
            return f"Local assault completed command={command}"
        if role == "tactical_intelligence":
            return f"Local tactical intelligence completed command={command}"
        if role == "track_threat":
            return f"Local track/threat analysis completed command={command}"
        if role == "task_scheduling":
            return f"Local task scheduling completed command={command}"
        if role == "simulation_execution":
            return f"Local simulation execution completed command={command}"
        if role == "closed_loop":
            return f"Local closed-loop optimization completed command={command}"
        return f"Local agent completed command={command}"

    @staticmethod
    def _unwrap_context_value(value):
        if isinstance(value, list):
            if not value:
                return {}
            return LocalAgentRuntime._unwrap_context_value(value[-1])
        if isinstance(value, dict) and "value" in value:
            return LocalAgentRuntime._unwrap_context_value(value["value"])
        return value

    @classmethod
    def _build_tactical_intelligence_result(cls, payload: dict) -> dict:
        mission_input = payload.get("input", {}).get("mission_input") or {}
        mission_id = payload.get("workflow_id") or "mission-demo"
        scenario_id = str(mission_input.get("scenario_id") or "")
        scene = mission_input.get("scene") or {
            "sector": "Sector-A",
            "protected_assets": [{"asset_id": "ASSET-001", "asset_name": "Command Post"}],
        }
        contacts = [
            item for item in mission_input.get("contacts") or []
            if isinstance(item, dict) and (item.get("contact_id") or item.get("track_id"))
        ]
        targets = mission_input.get("targets")
        if not targets and contacts:
            targets = []
            for index, contact in enumerate(contacts, start=1):
                metadata = contact.get("metadata") if isinstance(contact.get("metadata"), dict) else {}
                geo = contact.get("geo") if isinstance(contact.get("geo"), dict) else metadata.get("geo") or {}
                track_id = str(contact.get("contact_id") or contact.get("track_id"))
                if scenario_id == "maritime-convoy-air-defense":
                    hostile = index == len(contacts)
                    object_class = "fast_attack_craft" if hostile else "fishing_vessel"
                    label = "hostile" if hostile else "neutral"
                    score = 0.86 if hostile else 0.18
                    intent = "approach" if hostile else "civilian_transit"
                else:
                    object_class = str(contact.get("classification") or metadata.get("classification") or "unknown")
                    label = str(contact.get("affiliation") or metadata.get("affiliation") or "unknown")
                    score = float(metadata.get("confidence") or contact.get("confidence") or 0.6)
                    intent = contact.get("intent")
                targets.append({
                    "track_id": track_id,
                    "class": object_class,
                    "geo": geo,
                    "confidence": float(metadata.get("confidence") or contact.get("confidence") or 0.8),
                    "intent": intent,
                    "threat_score": score,
                    "metadata": {
                        "amos_track_id": track_id,
                        "source_class": object_class,
                        "label": label,
                        "affiliation": "red" if label == "hostile" else ("neutral" if label == "neutral" else "unknown"),
                        "threat_level": "high" if score >= 0.75 else ("medium" if score >= 0.45 else "low"),
                    },
                })
        targets = targets or [
            {
                "track_id": "T-001",
                "class": "hostile_uav",
                "geo": {"lat": 30.512, "lon": 114.381, "alt_m": 120.0},
                "confidence": 0.93,
                "intent": "reconnaissance",
                "threat_score": 0.84,
            },
            {
                "track_id": "T-002",
                "class": "hostile_vehicle",
                "geo": {"lat": 30.518, "lon": 114.389, "alt_m": 0.0},
                "confidence": 0.88,
                "intent": "approach",
                "threat_score": 0.72,
            },
        ]
        high_count = sum(
            1 for target in targets
            if str((target.get("metadata") or {}).get("threat_level") or "").lower() in {"high", "critical"}
        )
        low_count = sum(
            1 for target in targets
            if str((target.get("metadata") or {}).get("threat_level") or "").lower() in {"none", "low"}
        )
        return {
            "packet_id": f"{mission_id}-tia",
            "schema_version": "intelligence_packet/v1",
            "mission_id": mission_id,
            "scene": scene,
            "targets": targets,
            "tracks": targets,
            "summary": (
                f"Published tactical intelligence for {len(targets)} contacts: "
                f"{high_count} high-threat and {low_count} cleared/low-risk."
            ),
        }

    @staticmethod
    def _algolib_first_enabled() -> bool:
        return os.environ.get("A2A_FORCE_ALGOLIB_FIRST", "1").strip().lower() not in {
            "0", "false", "no", "off",
        }

    @staticmethod
    def _activity_skill(payload: dict) -> str:
        return str(payload.get("required_skill") or payload.get("command") or "")

    @classmethod
    def _algolib_pipeline_for(cls, role: str, payload: dict) -> list[str]:
        if role == "track_threat":
            return list(cls.ALGOLIB_ROLE_PIPELINE.get(cls._activity_skill(payload), []))
        return list(cls.ALGOLIB_ROLE_PIPELINE.get(role, []))

    @classmethod
    def _mission_input(cls, payload: dict) -> dict:
        input_payload = payload.get("input") if isinstance(payload.get("input"), dict) else {}
        mission = input_payload.get("mission_input")
        return mission if isinstance(mission, dict) else {}

    @classmethod
    def _media_refs(cls, payload: dict) -> list[dict]:
        mission = cls._mission_input(payload)
        refs = []
        for item in mission.get("attachments") or []:
            if not isinstance(item, dict):
                continue
            refs.append({
                "id": item.get("id") or item.get("media_id"),
                "uri": item.get("uri"),
                "mime_type": item.get("mime_type"),
                "name": item.get("name"),
            })
        for item in mission.get("evidence") or []:
            if not isinstance(item, dict):
                continue
            refs.append({
                "id": item.get("id") or item.get("media_id"),
                "uri": item.get("uri"),
                "mime_type": item.get("mime_type"),
                "name": item.get("source_name") or item.get("name"),
            })
        return [item for item in refs if item.get("uri") or item.get("id")]

    @classmethod
    def _frames(cls, payload: dict) -> list[dict]:
        mission = cls._mission_input(payload)
        frames = mission.get("perception_frames")
        if isinstance(frames, list) and frames:
            return deepcopy(frames)
        media_refs = cls._media_refs(payload)
        return [{"sensor_id": "mission_media", "modality": "mission_evidence", "payload": {"media_refs": media_refs}}] if media_refs else []

    @classmethod
    def _detections_from_value(cls, value: dict) -> list[dict]:
        detections = value.get("detections")
        if isinstance(detections, list):
            return deepcopy(detections)
        targets = value.get("targets") or value.get("tracks") or []
        rows = []
        for index, target in enumerate(targets if isinstance(targets, list) else [], start=1):
            if not isinstance(target, dict):
                continue
            rows.append({
                "detection_id": f"local-target-{index}",
                "track_id": target.get("track_id"),
                "class": target.get("class") or target.get("object_type"),
                "confidence": target.get("confidence", 0.8),
                "geo": target.get("geo") or target.get("current_point") or {},
                "metadata": target.get("metadata") or {},
            })
        return rows

    @classmethod
    def _tracks_from_value(cls, value: dict) -> list[dict]:
        tracks = value.get("tracks")
        if isinstance(tracks, list):
            return deepcopy(tracks)
        targets = value.get("targets")
        return deepcopy(targets) if isinstance(targets, list) else []

    @classmethod
    def _algolib_inputs(
        cls,
        algorithm_id: str,
        *,
        role: str,
        payload: dict,
        value: dict,
        outputs: dict[str, dict],
    ) -> dict:
        mission = cls._mission_input(payload)
        tracks = cls._tracks_from_value(value)
        detections = (
            outputs.get("battlefield_rtdetr_detector", {}).get("detections")
            or cls._detections_from_value(value)
        )
        verified = (
            outputs.get("edl_evidential_verifier", {}).get("verified_detections")
            or detections
        )
        batch_context = {
            "workflow_id": payload.get("workflow_id"),
            "role": role,
            "required_skill": cls._activity_skill(payload),
            "scenario_id": mission.get("scenario_id"),
            "phase": ((mission.get("stage_transfer") or {}).get("phase") if isinstance(mission.get("stage_transfer"), dict) else None),
            "contacts": len(mission.get("contacts") or []),
        }
        if algorithm_id == "battlefield_rtdetr_detector":
            return {"frames": cls._frames(payload), "media_refs": cls._media_refs(payload), "batch_context": batch_context}
        if algorithm_id == "edl_evidential_verifier":
            return {"detections": detections, "batch_context": batch_context}
        if algorithm_id == "motr_neural_kalman_tracker":
            return {
                "verified_detections": verified,
                "prior_tracks": tracks,
                "frames": cls._frames(payload),
                "batch_context": batch_context,
            }
        if algorithm_id == "multimodal_mamba_fusion":
            return {
                "embeddings": outputs.get("imagebind_multimodal_encoder", {}).get("embeddings") or {},
                "tracks": outputs.get("motr_neural_kalman_tracker", {}).get("tracks") or tracks,
                "batch_context": batch_context,
            }
        if algorithm_id == "supcon_meta_classifier":
            return {
                "fused_embeddings": outputs.get("multimodal_mamba_fusion", {}).get("fused_embeddings") or {},
                "support_shots": mission.get("support_shots") or [],
                "tracks": tracks,
                "batch_context": batch_context,
            }
        return {"tracks": tracks, "detections": detections, "batch_context": batch_context}

    @staticmethod
    def _result_summary(outputs: dict) -> dict:
        summary = {}
        for key in (
            "count", "track_count", "ranking_count", "scheduled_task_count",
            "detections", "verified_detections", "tracks", "classifications",
            "fused_embeddings", "damage_reports",
        ):
            value = outputs.get(key)
            if isinstance(value, list):
                summary[key] = len(value)
            elif isinstance(value, dict):
                summary[key] = len(value)
            elif value not in (None, "", []):
                summary[key] = value
        return summary

    @classmethod
    def _run_algolib_pipeline(cls, role: str, payload: dict, value: dict) -> tuple[list[dict], dict[str, dict], list[str]]:
        if not cls._algolib_first_enabled():
            return [], {}, []
        planned_ids = cls._algolib_pipeline_for(role, payload)
        if not planned_ids:
            return [], {}, []
        client = AlgorithmLibraryClient()
        try:
            active_rows = client.list_algorithms(active_only=True)
        except AlgorithmLibraryError as exc:
            return [], {}, [f"algolib_unavailable:{exc}"]
        active = {
            str(item.get("algorithm_id")): item
            for item in active_rows
            if isinstance(item, dict) and item.get("algorithm_id")
        }
        calls = []
        outputs_by_algorithm: dict[str, dict] = {}
        warnings = []
        for algorithm_id in planned_ids:
            meta = active.get(algorithm_id)
            if not meta:
                warnings.append(f"algolib_algorithm_not_active:{algorithm_id}")
                continue
            inputs = cls._algolib_inputs(
                algorithm_id,
                role=role,
                payload=payload,
                value=value,
                outputs=outputs_by_algorithm,
            )
            start = time.perf_counter()
            try:
                outputs = client.predict(
                    algorithm_id,
                    inputs,
                    request_id=str(payload.get("work_item") or payload.get("workflow_id") or ""),
                    trace_id=str(payload.get("workflow_id") or ""),
                    version=str(meta.get("version") or "1.0.0"),
                    backend_type=str(meta.get("backend_type") or "python_http_service"),
                )
            except AlgorithmLibraryError as exc:
                warnings.append(f"algolib_call_failed:{algorithm_id}:{exc}")
                continue
            duration_ms = round((time.perf_counter() - start) * 1000.0, 3)
            outputs_by_algorithm[algorithm_id] = outputs
            model_profile = meta.get("model_profile") if isinstance(meta.get("model_profile"), dict) else {}
            calls.append({
                "algorithm_id": algorithm_id,
                "algorithm_name": algorithm_id,
                "model_id": meta.get("model_id") or model_profile.get("model_id"),
                "version": meta.get("version"),
                "backend_type": meta.get("backend_type"),
                "status": "completed",
                "execution_mode": "algolib_runtime",
                "duration_ms": duration_ms,
                "input_summary": {
                    "frames": len(inputs.get("frames") or []),
                    "detections": len(inputs.get("detections") or inputs.get("verified_detections") or []),
                    "tracks": len(inputs.get("tracks") or inputs.get("prior_tracks") or []),
                },
                "result_summary": cls._result_summary(outputs),
            })
        return calls, outputs_by_algorithm, warnings

    @classmethod
    def _attach_algolib_evidence(cls, role: str, payload: dict, value: dict) -> dict:
        if not isinstance(value, dict):
            return value
        calls, outputs, warnings = cls._run_algolib_pipeline(role, payload, value)
        if calls:
            selected = list(value.get("selected_algorithms") or [])
            for call in calls:
                algorithm_id = call["algorithm_id"]
                if algorithm_id not in selected:
                    selected.append(algorithm_id)
            value["selected_algorithms"] = selected
            value["algorithm_calls"] = list(value.get("algorithm_calls") or []) + calls
            value["algolib_outputs"] = outputs
            value["execution_mode"] = "local_agent_with_algolib_runtime"
        else:
            value.setdefault("execution_mode", "local_agent")
        if warnings:
            value.setdefault("warnings", []).extend(warnings)
        return value

    @staticmethod
    def _attach_decision_algorithm_calls(response: dict, agent_name: str, agent_response) -> None:
        output = response.get("output") if isinstance(response.get("output"), dict) else {}
        selected = list(getattr(agent_response, "selected_algorithms", []) or [])
        if not selected:
            return
        key = "decision_planning_result" if agent_name == "decision_planning_agent" else "compliance_authorization_result"
        result = output.get(key)
        if not isinstance(result, dict):
            result = {}
            output[key] = result
        existing = list(result.get("algorithm_calls") or [])
        for algorithm_id in selected:
            if any(row.get("algorithm_id") == algorithm_id for row in existing if isinstance(row, dict)):
                continue
            existing.append({
                "algorithm_id": algorithm_id,
                "algorithm_name": algorithm_id,
                "status": "completed" if response.get("status") == "completed" else response.get("status"),
                "execution_mode": "local_algorithm",
            })
        result["algorithm_calls"] = existing
        result["selected_algorithms"] = selected
        if selected:
            result.setdefault("execution_mode", "local_algorithm")
        response["output"] = output

    @classmethod
    def _build_tracking_result(cls, payload: dict) -> dict:
        cognition = cls._unwrap_context_value(payload.get("input", {}).get("cognition_result"))
        targets = cognition.get("targets") if isinstance(cognition, dict) else None
        targets = targets or [
            {
                "track_id": "T-001",
                "class": "hostile_uav",
                "geo": {"lat": 30.512, "lon": 114.381},
                "threat_score": 0.84,
            }
        ]
        tracks = []
        target_histories = []
        for index, target in enumerate(targets, start=1):
            track_id = str(target.get("track_id") or f"T-{index:03d}")
            score = float(target.get("threat_score") or 0.6)
            tracks.append(
                {
                    "track_id": track_id,
                    "object_type": target.get("class", "unknown"),
                    "current_point": target.get("geo", {}),
                    "metadata": target.get("metadata", {}),
                    "history_points": 5,
                    "threat_score": score,
                    "confidence": float(target.get("confidence") or 0.8),
                }
            )
            target_histories.append(
                {
                    "target_id": track_id,
                    "steps": [
                        {
                            "timestamp": "2026-08-12T00:00:00Z",
                            "risk_score": round(score * 100.0, 2),
                            "probability": round(score, 4),
                            "priority": index,
                            "resource_pressure": 0.35,
                        }
                    ],
                }
            )
        return {
            "schema_version": "tracking_result/v1",
            "tracks": tracks,
            "target_histories": target_histories,
            "scene": cognition.get("scene", {}) if isinstance(cognition, dict) else {},
            "summary": {"track_count": len(tracks)},
        }

    @classmethod
    def _build_threat_assessment_result(cls, payload: dict) -> dict:
        tracking = cls._unwrap_context_value(payload.get("input", {}).get("tracking_result"))
        tracks = tracking.get("tracks") if isinstance(tracking, dict) else []
        risk_assessments = []
        ranking = []
        for index, track in enumerate(tracks or [], start=1):
            target_id = str(track.get("track_id") or f"T-{index:03d}")
            probability = round(float(track.get("threat_score") or 0.6), 4)
            risk = "high" if probability >= 0.75 else ("medium" if probability >= 0.45 else "low")
            risk_assessments.append(
                {
                    "target_id": target_id,
                    "priority": index,
                    "risk": risk,
                    "threat_score": round(probability * 100.0, 2),
                    "probability": probability,
                    "rationale": f"Target {target_id} remains high-interest after tracking fusion.",
                    "triggered_rules": ["tracking_continuity", "trajectory_risk_rank"],
                }
            )
            ranking.append(
                {
                    "rank": index,
                    "item_type": "track",
                    "item_id": target_id,
                    "score": probability,
                    "level": risk,
                    "reason": f"Priority derived from track {target_id} threat score.",
                }
            )
        return {
            "schema_version": "threat_assessment_result/v1",
            "tracks": tracks or [],
            "risk_assessments": risk_assessments,
            "unified_threat_ranking": ranking,
            "target_histories": tracking.get("target_histories", []) if isinstance(tracking, dict) else [],
            "summary": {"risk_count": len(risk_assessments)},
        }

    @classmethod
    def _build_task_scheduling_result(cls, payload: dict) -> dict:
        threat = cls._unwrap_context_value(payload.get("input", {}).get("threat_assessment_result"))
        risks = threat.get("risk_assessments") if isinstance(threat, dict) else []
        scheduled_tasks = []
        for index, risk in enumerate(risks or [], start=1):
            scheduled_tasks.append(
                {
                    "id": f"TASK-{index:03d}",
                    "target_id": risk.get("target_id"),
                    "priority": int(risk.get("priority") or index),
                    "task_type": "engage" if risk.get("risk") == "high" else "monitor",
                    "deadline": f"2026-08-12T00:{index:02d}:00Z",
                    "required_resource_types": ["sensor", "artillery"],
                }
            )
        resources = [
            {
                "id": "SENSOR-001",
                "type": "sensor",
                "status": "available",
                "capacity": 1.0,
                "location": "Sector-A",
                "attributes": {"modality": "eo_ir"},
            },
            {
                "id": "FIRE-001",
                "type": "artillery",
                "status": "available",
                "capacity": 1.0,
                "location": "Sector-A",
                "attributes": {"ammo": 8},
            },
        ]
        return {
            "mission_id": payload.get("workflow_id"),
            "scheduled_tasks": scheduled_tasks,
            "resources": resources,
            "risk_assessments": deepcopy(risks or []),
            "target_histories": deepcopy(threat.get("target_histories", []) if isinstance(threat, dict) else []),
            "planning_objectives": [
                "优先处置高风险目标",
                "保持关键资源可用性",
            ],
            "summary": {
                "scheduled_task_count": len(scheduled_tasks),
                "resource_count": len(resources),
            },
        }

    @classmethod
    def _build_algolib_task_scheduling_result(cls, payload: dict) -> dict:
        from task_scheduling_agent.agent import TaskSchedulingAgent
        from task_scheduling_agent.main import (
            _runtime_config,
            build_scheduler_input,
            normalize_task_scheduling_result,
        )

        scheduler_input = build_scheduler_input(payload)
        scheduler = TaskSchedulingAgent(use_mock=True, config=_runtime_config())
        raw_result = scheduler.run(scheduler_input)
        value = normalize_task_scheduling_result(payload, raw_result)
        algorithm_calls = []
        algolib_result = value.get("algolib_result") if isinstance(value.get("algolib_result"), dict) else {}
        for algorithm_id in value.get("selected_algorithms") or []:
            algorithm_calls.append({
                "algorithm_id": algorithm_id,
                "algorithm_name": algorithm_id,
                "version": algolib_result.get("version"),
                "backend_type": algolib_result.get("backend_type"),
                "status": "completed",
                "execution_mode": "algolib_runtime",
                "input_summary": {
                    "tasks": len(scheduler_input.get("tasks") or []),
                    "platforms": len(scheduler_input.get("platforms") or []),
                    "phase": scheduler_input.get("phase"),
                },
                "result_summary": {
                    "scheduled_tasks": len(value.get("scheduled_tasks") or []),
                    "resources": len(value.get("resources") or []),
                    "sensor_assignments": len(value.get("sensor_assignments") or []),
                },
            })
        value["algorithm_calls"] = algorithm_calls
        value["execution_mode"] = "local_agent_with_algolib_runtime" if algorithm_calls else "local_agent"
        return value

    def _output_for(self, role: str, payload: dict) -> tuple[dict, str]:
        output_hint = payload.get("output_hint") or "result"
        message = self._message_for(role, payload)

        if role == "recon":
            value = "Sector_A is heavily fortified with overlapping machine gun nests."
        elif role == "tactical_intelligence":
            value = self._build_tactical_intelligence_result(payload)
            value = self._attach_algolib_evidence(role, payload, value)
        elif role == "track_threat":
            skill_id = payload.get("required_skill") or payload.get("command")
            if skill_id == "trajectory_tracking":
                value = self._build_tracking_result(payload)
            else:
                value = self._build_threat_assessment_result(payload)
            value = self._attach_algolib_evidence(role, payload, value)
        elif role == "task_scheduling":
            try:
                value = self._build_algolib_task_scheduling_result(payload)
            except Exception as exc:
                value = self._build_task_scheduling_result(payload)
                value["execution_mode"] = "local_agent"
                value["algorithm_calls"] = []
                value.setdefault("warnings", []).append(f"algolib_task_scheduling_fallback:{exc}")
        elif role == "execution_control":
            from execution_control_agent.algolib_runtime import run_execution_control_with_backend
            from execution_control_agent.main import build_execution_control_arguments

            value = run_execution_control_with_backend(build_execution_control_arguments(payload))
        elif role == "simulation_execution":
            from execution_control_agent.algolib_runtime import run_execution_control_with_backend
            from execution_control_agent.main import build_execution_control_arguments

            value = run_execution_control_with_backend(build_execution_control_arguments(payload))
        elif role == "artillery":
            from artillery_agent.main import execute_artillery_command

            structured, _message = execute_artillery_command(payload)
            value = structured
        elif role == "evaluator":
            value = int(payload.get("input", {}).get("mock_eval_score", 40))
        elif role == "assault":
            from assault_agent.main import execute_assault_command

            structured, _message = execute_assault_command(payload)
            value = structured
        elif role == "closed_loop":
            from closed_loop_agent.algolib_runtime import run_closed_loop_with_backend
            from closed_loop_agent.main import build_closed_loop_arguments

            value = run_closed_loop_with_backend(build_closed_loop_arguments(payload))
        else:
            value = message
        return {output_hint: value}, message
