from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _contact_targets(mission_input: Dict[str, Any]) -> List[Dict[str, Any]]:
    targets: List[Dict[str, Any]] = []
    for index, contact in enumerate(mission_input.get("contacts", []), start=1):
        target_kind = str(contact.get("kind", "unknown"))
        object_class = "ship"
        if "uav" in target_kind or "drone" in target_kind:
            object_class = "uav"
        elif "air" in target_kind:
            object_class = "aircraft"
        elif "surface" in target_kind or "ship" in target_kind:
            object_class = "ship"
        location = str(contact.get("location", f"Sector-{index}"))
        lat = 30.5 + (index * 0.012)
        lon = 114.3 + (index * 0.015)
        targets.append(
            {
                "track_id": f"T-{index:04d}",
                "class": object_class,
                "label": "hostile" if float(contact.get("threat_level", 0.5)) >= 0.6 else "unknown",
                "affiliation": "red" if float(contact.get("threat_level", 0.5)) >= 0.6 else "unknown",
                "threat_level": "high" if float(contact.get("threat_level", 0.5)) >= 0.75 else "medium",
                "geo": {"lat": lat, "lon": lon, "alt_m": 120.0 if object_class != "ship" else 0.0},
                "confidence": round(min(0.99, 0.55 + float(contact.get("threat_level", 0.5)) * 0.4), 4),
                "knowledge_ref": f"ENT-{contact.get('contact_id', location)}",
                "source_contact_id": contact.get("contact_id"),
                "source_location": location,
                "intent": contact.get("intent"),
            }
        )
    return targets


def _tracking_algorithm_catalog(prediction_model: str, fused_model: str) -> Dict[str, List[str]]:
    readable_prediction = prediction_model.replace("_", " ")
    readable_fused = fused_model.replace("_", " ")
    return {
        "tracking": [
            "gated nearest-neighbor association + kalman-like state update",
            f"adaptive motion model selection ({readable_prediction})",
            f"IMM fused constant-velocity / constant-acceleration / coordinated-turn prediction ({readable_fused})",
            "ST-GNN-inspired graph-neighbor trajectory refinement",
        ],
        "ranking": [
            "weighted multi-factor threat scoring",
            "DBN-inspired posterior threat smoothing",
            "XAI evidence-chain generation",
        ],
        "grouping": [
            "distance-heading-speed cohesion grouping",
            "group-level attention scoring with member threat fusion",
        ],
        "asset_impact": [
            "protected-asset proximity and closing-rate impact scoring",
        ],
    }


def _planning_algorithm_catalog() -> Dict[str, List[str]]:
    return {
        "planning": [
            "template-based candidate plan generation",
            "multi-factor weighted baseline scoring",
            "logistic recommendation scoring",
            "lightweight LSTM-style target trend scoring",
            "RAG evidence enhancement",
        ]
    }


def _compliance_algorithm_catalog() -> Dict[str, List[str]]:
    return {
        "compliance": [
            "rule-table compliance checks",
            "authorization scope consistency checks",
            "RAG evidence retrieval",
            "logistic risk calibration",
        ]
    }


def _frame_summaries_from_artifacts(frame_artifacts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    summaries: List[Dict[str, Any]] = []
    for index, artifact in enumerate(frame_artifacts):
        ranking = artifact.get("unified_threat_ranking") or []
        top_rank = ranking[0] if ranking else {}
        summaries.append(
            {
                "frame_index": index,
                "track_count": len(artifact.get("tracks") or []),
                "group_count": len(artifact.get("groups") or []),
                "top_rank": {
                    "entity_type": top_rank.get("entity_type"),
                    "entity_id": top_rank.get("entity_id") or top_rank.get("item_id"),
                    "score": top_rank.get("score"),
                },
            }
        )
    return summaries


def _ranking_history(frame_artifacts: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    history: Dict[str, List[Dict[str, Any]]] = {}
    for frame_index, artifact in enumerate(frame_artifacts):
        ranking = artifact.get("unified_threat_ranking") or []
        for item in ranking:
            entity_id = str(item.get("entity_id") or item.get("item_id") or "")
            if not entity_id:
                continue
            history.setdefault(entity_id, []).append(
                {
                    "timestamp": f"2026-06-26T00:{frame_index:02d}:00Z",
                    "risk_score": round(float(item.get("score", 0.0)) * 100.0, 2),
                    "probability": round(float(item.get("score", 0.0)), 4),
                    "priority": int(item.get("rank", frame_index + 1) or frame_index + 1),
                    "resource_pressure": round(min(0.95, 0.35 + frame_index * 0.06), 2),
                }
            )
    return history


def _prepare_tracking_frames(mission_input: Dict[str, Any], workflow_id: str) -> List[Dict[str, Any]]:
    frames = mission_input.get("perception_frames") or []
    prepared: List[Dict[str, Any]] = []
    for index, frame in enumerate(frames):
        prepared_frame = dict(frame)
        prepared_frame.setdefault("task_id", f"{workflow_id}-frame-{index:02d}")
        prepared_frame.setdefault("message_type", "perception_result")
        prepared_frame.setdefault("algorithm_level", "medium")
        if not prepared_frame.get("scene"):
            prepared_frame["scene"] = dict(mission_input.get("scene") or {})
        if not prepared_frame["scene"].get("protected_assets") and mission_input.get("protected_assets"):
            prepared_frame["scene"]["protected_assets"] = mission_input.get("protected_assets")
        prepared.append(prepared_frame)
    return prepared


def run_cognition(blackboard: Dict[str, Any]) -> Dict[str, Any]:
    from agent.pipeline import create_agent
    from tactical_intelligence_agent.payload_adapter import commander_payload_to_batch

    mission_input = blackboard["mission_input"]
    contacts = mission_input.get("contacts", [])
    fake_attachments = []
    for index, _ in enumerate(contacts or [None], start=1):
        fake_attachments.append(
            {
                "id": f"mock-image-{index:03d}",
                "uri": f"http://example.com/mock-image-{index:03d}.jpg",
                "kind": "image",
                "checksum": {"algorithm": "sha256", "value": f"mock-checksum-{index:03d}"},
            }
        )

    payload = {
        "workflow_id": blackboard["workflow_id"],
        "command": "buildSituationSummary",
        "attachments": fake_attachments,
        "input": {
            "recon_report": mission_input.get("intelligence_text") or mission_input.get("objective"),
            "sector": contacts[0].get("location") if contacts else "sector-unknown",
        },
        "context": {
            "subscriber_agents": ["commander", "track_threat_agent"],
            "jamming_level": float(mission_input.get("environment", {}).get("jamming_level", 0.1) or 0.1),
        },
    }
    agent = create_agent({"use_mock": os.environ.get("INTEGRATED_TIA_USE_MOCK", "1") != "0"})
    batch = commander_payload_to_batch(payload)
    packet = agent.process(batch).model_dump(mode="json")
    provenance = packet.get("provenance") or {}

    branch_targets = packet.get("targets") or []
    contact_targets = _contact_targets(mission_input)
    if contact_targets:
        if not branch_targets:
            packet["targets"] = contact_targets
        else:
            merged_targets = []
            for index, target in enumerate(branch_targets):
                contact_target = contact_targets[index] if index < len(contact_targets) else {}
                merged = dict(target)
                merged.update(
                    {
                        "track_id": contact_target.get("track_id", target.get("track_id")),
                        "class": contact_target.get("class", target.get("class")),
                        "label": contact_target.get("label", target.get("label")),
                        "affiliation": contact_target.get("affiliation", target.get("affiliation")),
                        "threat_level": contact_target.get("threat_level", target.get("threat_level")),
                        "geo": contact_target.get("geo", target.get("geo")),
                        "confidence": max(
                            float(target.get("confidence", 0.0)),
                            float(contact_target.get("confidence", 0.0)),
                        ),
                        "knowledge_ref": contact_target.get("knowledge_ref", target.get("knowledge_ref")),
                        "source_contact_id": contact_target.get("source_contact_id"),
                        "source_location": contact_target.get("source_location"),
                        "intent": contact_target.get("intent"),
                    }
                )
                merged_targets.append(merged)
            if len(contact_targets) > len(merged_targets):
                merged_targets.extend(contact_targets[len(merged_targets) :])
            packet["targets"] = merged_targets

    return {
        "status": "completed",
        "capability": "cognition",
        "agent": "tactical_intelligence_agent",
        "result": {
            "situation_summary": packet.get("summary", ""),
            "intelligence_packet": packet,
            "target_count": len(packet.get("targets", [])),
            "routing": packet.get("routing", {}),
        },
        "confidence": 0.86,
        "evidence": ["Used cms tactical_intelligence_agent branch pipeline."],
        "warnings": [],
        "next_suggestion": "continue",
        "meta": {
            "execution_mode": "branch_pipeline_mock_driven",
            "algorithm_catalog": {
                "perception": list((provenance.get("perception") or {}).keys()),
                "cognition": list((provenance.get("cognition") or {}).keys()),
                "communication": list((provenance.get("communication") or {}).keys()),
            },
            "uses_mock_inputs": True,
            "uses_mock_agent": os.environ.get("INTEGRATED_TIA_USE_MOCK", "1") != "0",
        },
    }


def run_tracking(blackboard: Dict[str, Any]) -> Dict[str, Any]:
    from track_threat_agent.app import main as tt_main

    mission_input = blackboard["mission_input"]
    tt_main.algorithm_provider.reset()
    tt_main.runtime.reset_runtime()
    tt_main.reset_adapter_cache()

    frame_artifacts: List[Dict[str, Any]] = []
    prepared_frames = _prepare_tracking_frames(mission_input, blackboard["workflow_id"])
    if prepared_frames:
        result = None
        for frame in prepared_frames:
            perception = tt_main.PerceptionResultRequest.model_validate(frame)
            result = tt_main._process_payload(perception)
            frame_artifacts.append(result["artifact"])
        assert result is not None
    else:
        cognition = blackboard.get("results", {}).get("cognition", {}).get("result", {})
        packet = dict(cognition.get("intelligence_packet") or {})
        if not packet:
            packet = {
                "targets": _contact_targets(mission_input),
                "mission_id": blackboard["workflow_id"],
                "algorithm_level": "medium",
            }
        packet.setdefault("mission_id", blackboard["workflow_id"])
        packet.setdefault("algorithm_level", "medium")
        packet.setdefault(
            "scene",
            {
                "protected_assets": mission_input.get("protected_assets")
                or [
                    {
                        "asset_id": "objective-zone",
                        "asset_name": mission_input.get("objective", "mission-area"),
                        "asset_type": "civil_infrastructure",
                        "lat": 30.55,
                        "lon": 114.32,
                        "protection_radius_m": 30000,
                        "criticality": 0.9,
                    }
                ]
            },
        )
        perception = tt_main._perception_from_a2a_task(
            {"task_id": blackboard["workflow_id"], "input": packet}
        )
        result = tt_main._process_payload(perception)
        frame_artifacts.append(result["artifact"])

    artifact = result["artifact"]
    tracks = artifact.get("tracks") or []
    first_track = tracks[0] if tracks else {}
    first_prediction = (first_track.get("predicted_path") or [{}])[0]
    first_metadata = first_track.get("metadata") or {}
    prediction_meta = first_metadata.get("prediction") or {}
    frame_summaries = _frame_summaries_from_artifacts(frame_artifacts)
    return {
        "status": "completed",
        "capability": "tracking",
        "agent": "track_threat_agent",
        "result": {
            "artifact": artifact,
            "track_count": len(artifact.get("tracks", [])),
            "group_count": len(artifact.get("groups", [])),
            "top_rank": (artifact.get("unified_threat_ranking") or [{}])[0],
            "frames_processed": len(frame_artifacts),
            "frame_summaries": frame_summaries,
            "ranking_history": _ranking_history(frame_artifacts),
        },
        "confidence": 0.84,
        "evidence": ["Used wc track_threat_agent branch processing pipeline."],
        "warnings": [],
        "next_suggestion": "continue",
        "meta": {
            "execution_mode": "branch_builtin_algorithm",
            "algorithm_catalog": _tracking_algorithm_catalog(
                prediction_meta.get("model") or "adaptive_constant_velocity",
                first_prediction.get("model_used") or "imm_fused_graph_refined",
            ),
        },
    }


def _decision_request(blackboard: Dict[str, Any]) -> Dict[str, Any]:
    mission_input = blackboard["mission_input"]
    tracking = blackboard.get("results", {}).get("tracking", {}).get("result", {}).get("artifact", {})
    ranked = tracking.get("unified_threat_ranking") or []
    risks = []
    tasks = []
    histories = []
    history_by_target = tracking.get("ranking_history") or {}
    for index, item in enumerate(ranked[:6], start=1):
        target_id = str(item.get("entity_id") or item.get("item_id") or f"target-{index:03d}")
        score = float(item.get("score", 0.5))
        risks.append(
            {
                "target_id": target_id,
                "priority": index,
                "risk": "high" if score >= 0.75 else "medium",
                "threat_score": round(score * 100.0, 2),
                "probability": min(0.99, max(0.01, score)),
                "rationale": f"Derived from track_threat_agent ranking level={item.get('level')}",
            }
        )
        tasks.append(
            {
                "id": f"task-{index:03d}",
                "target_id": target_id,
                "priority": index,
                "task_type": "contain_or_strike",
                "required_resource_types": ["uav", "artillery"],
            }
        )
        history_steps = history_by_target.get(target_id) or [
            {
                "timestamp": "2026-06-26T00:00:00Z",
                "risk_score": round(score * 100.0, 2),
                "probability": min(0.99, max(0.01, score)),
                "priority": index,
                "resource_pressure": 0.45,
            }
        ]
        histories.append({"target_id": target_id, "steps": history_steps})
    resources = []
    for platform in mission_input.get("friendly_platforms", []):
        resources.append(
            {
                "id": platform.get("platform_id"),
                "type": platform.get("platform_type", "generic"),
                "status": "available" if float(platform.get("readiness", 0.0)) >= 0.6 else "busy",
                "capacity": float(platform.get("readiness", 0.0)),
                "location": platform.get("location"),
                "attributes": {"munitions": platform.get("munitions", 0)},
            }
        )
    constraints = []
    for key, value in (mission_input.get("constraints") or {}).items():
        constraints.append({"name": key, "value": value})
    return {
        "request_id": blackboard["workflow_id"],
        "agent_profile": {"compute_budget": "medium", "risk_policy": "balanced"},
        "risk_assessments": risks,
        "scheduled_tasks": tasks,
        "resources": resources,
        "target_histories": histories,
        "planning_objectives": [mission_input.get("objective", "mission objective")],
        "constraints": constraints,
        "authorization": {"status": "approved" if not mission_input.get("require_operator_approval") else "pending_review"},
    }


def run_decision_planning(blackboard: Dict[str, Any]) -> Dict[str, Any]:
    from decision_agents.common.a2a_payloads import run_agent_payload
    from decision_agents.decision_planning.agent import DecisionPlanningAgent

    payload = {"workflow_id": blackboard["workflow_id"], "input": {"agent_request": _decision_request(blackboard)}}
    response = run_agent_payload(DecisionPlanningAgent(), "decision_planning_agent", payload)
    response_payload = response.model_dump(mode="json")
    return {
        "status": "completed",
        "capability": "decision_planning",
        "agent": "decision_planning_agent",
        "result": response_payload.get("result", {}),
        "confidence": 0.87,
        "evidence": ["Used lzh decision_planning_agent branch algorithm."],
        "warnings": response_payload.get("warnings", []),
        "next_suggestion": "continue",
        "meta": {
            "agent_response": response_payload,
            "execution_mode": "branch_builtin_algorithm",
            "algorithm_catalog": _planning_algorithm_catalog(),
        },
    }


def run_compliance(blackboard: Dict[str, Any]) -> Dict[str, Any]:
    from decision_agents.common.a2a_payloads import run_agent_payload
    from decision_agents.compliance_authorization.agent import ComplianceAuthorizationAgent

    request_payload = _decision_request(blackboard)
    planning_result = blackboard.get("results", {}).get("decision_planning", {}).get("result", {})
    request_payload["candidate_plans"] = planning_result.get("candidate_plans", [])
    request_payload["authorization"] = {
        "status": (
            "approved"
            if blackboard.get("operator", {}).get("approval_override") is True
            or not blackboard["mission_input"].get("require_operator_approval")
            else "pending_review"
        ),
        "approved_plan_ids": [planning_result.get("recommended_plan_id")] if planning_result.get("recommended_plan_id") else [],
        "scope": [blackboard["mission_input"].get("objective", "mission objective")],
    }
    payload = {"workflow_id": blackboard["workflow_id"], "input": {"agent_request": request_payload}}
    response = run_agent_payload(ComplianceAuthorizationAgent(), "compliance_authorization_agent", payload)
    response_payload = response.model_dump(mode="json")
    result = response_payload.get("result", {})
    decision = str(result.get("decision", "review_required"))
    authorized = decision == "approved"
    return {
        "status": "completed",
        "capability": "compliance_authorization",
        "agent": "compliance_authorization_agent",
        "result": {
            **result,
            "authorized": authorized,
        },
        "confidence": 0.9,
        "evidence": ["Used lzh compliance_authorization_agent branch algorithm."],
        "warnings": response_payload.get("warnings", []),
        "next_suggestion": "continue" if authorized else "operator_review",
        "meta": {
            "agent_response": response_payload,
            "execution_mode": "branch_builtin_algorithm",
            "algorithm_catalog": _compliance_algorithm_catalog(),
        },
    }


def run_closed_loop_execution(blackboard: Dict[str, Any]) -> Dict[str, Any]:
    from closed_loop_agent.closed_loop_core import _closed_loop_optimization

    cognition = blackboard.get("results", {}).get("cognition", {}).get("result", {})
    tracking = blackboard.get("results", {}).get("tracking", {}).get("result", {})
    threat = tracking.get("artifact", {})
    arguments = {
        "cycles": 1,
        "target_count": max(5, len(threat.get("tracks", [])) or len(blackboard["mission_input"].get("contacts", []))),
        "results": {
            "perception_detection": {
                "output_data": {"detections": [{"conf": 0.88}]}
            },
            "recognition": {
                "output_data": {
                    "confidence": 0.82,
                    "target_class": (cognition.get("intelligence_packet", {}).get("targets", [{}])[0] or {}).get("class", "unknown"),
                }
            },
            "threat_evaluation": {
                "output_data": {
                    "threat_score": float(((threat.get("unified_threat_ranking") or [{}])[0] or {}).get("score", 0.68))
                }
            },
            "resource_allocation": {
                "output_data": {"ammo_pressure": 0.4}
            },
        },
    }
    result = _closed_loop_optimization(arguments)
    output_data = result.get("output_data", {})
    execution = output_data.get("execution_control", {})
    algorithm_catalog = output_data.get("algorithm", {})
    return {
        "status": "completed",
        "capability": "execution_control",
        "agent": "closed_loop_agent",
        "result": {
            "closed_loop_output": output_data,
            "execution_status": "completed",
            "completion_ratio": output_data.get("closed_loop_optimization", {}).get("mission_completion_final", 0.0),
            "resource_consumption": {"platforms_committed": len(blackboard["mission_input"].get("friendly_platforms", []))},
            "command_count": len(execution.get("commands", [])),
            "simulated_score": output_data.get("closed_loop_optimization", {}).get("mission_completion_final", 0.0),
        },
        "confidence": 0.88,
        "evidence": ["Used zh closed_loop_agent branch optimization pipeline."],
        "warnings": [],
        "next_suggestion": "continue",
        "meta": {
            "execution_mode": "branch_builtin_algorithm_simulation_only",
            "algorithm_catalog": algorithm_catalog,
        },
    }


def main() -> None:
    payload = json.load(sys.stdin)
    capability = payload["capability"]
    blackboard = payload["blackboard"]
    if capability == "cognition":
        result = run_cognition(blackboard)
    elif capability == "tracking":
        result = run_tracking(blackboard)
    elif capability == "decision_planning":
        result = run_decision_planning(blackboard)
    elif capability == "compliance_authorization":
        result = run_compliance(blackboard)
    elif capability == "execution_control":
        result = run_closed_loop_execution(blackboard)
    else:
        raise ValueError(f"Unsupported capability: {capability}")
    json.dump(result, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
