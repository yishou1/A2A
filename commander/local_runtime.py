import json
import time
from copy import deepcopy
from typing import Dict, Iterable, Tuple

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
            extra={"mode": "local"},
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
                extra={"mode": "local", "token": token},
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
        scene = mission_input.get("scene") or {
            "sector": "Sector-A",
            "protected_assets": [{"asset_id": "ASSET-001", "asset_name": "Command Post"}],
        }
        targets = mission_input.get("targets") or [
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
        return {
            "packet_id": f"{mission_id}-tia",
            "schema_version": "intelligence_packet/v1",
            "mission_id": mission_id,
            "scene": scene,
            "targets": targets,
            "tracks": targets,
            "summary": "Detected 2 hostile targets and published shared tactical intelligence.",
        }

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
            risk = "high" if probability >= 0.75 else "medium"
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

    def _output_for(self, role: str, payload: dict) -> tuple[dict, str]:
        output_hint = payload.get("output_hint") or "result"
        message = self._message_for(role, payload)

        if role == "recon":
            value = "Sector_A is heavily fortified with overlapping machine gun nests."
        elif role == "tactical_intelligence":
            value = self._build_tactical_intelligence_result(payload)
        elif role == "track_threat":
            skill_id = payload.get("required_skill") or payload.get("command")
            if skill_id == "trajectory_tracking":
                value = self._build_tracking_result(payload)
            else:
                value = self._build_threat_assessment_result(payload)
        elif role == "task_scheduling":
            value = self._build_task_scheduling_result(payload)
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
