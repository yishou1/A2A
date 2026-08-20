#!/usr/bin/env python3
"""Bootstrap algorithm packages, service entrypoints, and golden cases."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ROOT / "services"
sys.path.insert(0, str(SERVICES))

from a2a_algorithms_common.association_rules import discretize_situation, load_or_mine_rules, match_rules, choose_primary_rule
from a2a_algorithms_common.closed_loop_advisor import advise
from a2a_algorithms_common.execution_planner import run_planner
from a2a_algorithms_common.mission_feature_adapter import build_features_from_agent_results, build_features_from_sc2le_proxy
from a2a_algorithms_common.mission_scorer import score_mission
from a2a_algorithms_common.motion_prediction import predict_single_track

ALGORITHMS = [
    {
        "algorithm_id": "execution_rule_matcher",
        "display_name": "Execution Rule Matcher",
        "port": 9010,
        "task_family": "decision",
        "primary_metric": "rule_match_rate",
        "primary_score": 0.85,
        "risk_level": "medium",
    },
    {
        "algorithm_id": "trajectory_linear_predictor",
        "display_name": "Trajectory Linear Predictor",
        "port": 9011,
        "task_family": "forecasting",
        "primary_metric": "trajectory_fit_mae",
        "primary_score": 0.90,
        "risk_level": "low",
    },
    {
        "algorithm_id": "execution_control_planner",
        "display_name": "Execution Control Planner",
        "port": 9012,
        "task_family": "planning",
        "primary_metric": "command_generation_success",
        "primary_score": 0.88,
        "risk_level": "medium",
    },
    {
        "algorithm_id": "mission_feature_adapter",
        "display_name": "Mission Feature Adapter",
        "port": 9013,
        "task_family": "feature_engineering",
        "primary_metric": "schema_compliance",
        "primary_score": 1.0,
        "risk_level": "low",
    },
    {
        "algorithm_id": "mission_completion_scorer",
        "display_name": "Mission Completion Scorer",
        "port": 9014,
        "task_family": "scoring",
        "primary_metric": "classification_accuracy",
        "primary_score": 0.5743,
        "risk_level": "medium",
    },
    {
        "algorithm_id": "closed_loop_decision_advisor",
        "display_name": "Closed Loop Decision Advisor",
        "port": 9015,
        "task_family": "decision",
        "primary_metric": "policy_consistency",
        "primary_score": 0.90,
        "risk_level": "medium",
    },
]

INPUT_SCHEMAS = {
    "execution_rule_matcher": {
        "title": "Execution Rule Matcher Input",
        "type": "object",
        "required": ["phase", "situation"],
        "properties": {
            "phase": {"type": "string", "enum": ["strike", "assault"]},
            "situation": {
                "type": "object",
                "required": ["threat_score", "intel_confidence", "resource_readiness"],
                "properties": {
                    "threat_score": {"type": "number", "minimum": 0, "maximum": 1},
                    "intel_confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "resource_readiness": {"type": "number", "minimum": 0, "maximum": 1},
                    "communication_quality": {"type": "number", "minimum": 0, "maximum": 1},
                    "commander_decision": {"type": "string"},
                },
            },
        },
        "additionalProperties": False,
    },
    "trajectory_linear_predictor": {
        "title": "Trajectory Linear Predictor Input",
        "type": "object",
        "required": ["track"],
        "properties": {
            "track": {
                "type": "object",
                "required": ["history"],
                "properties": {
                    "track_id": {"type": "string"},
                    "history": {"type": "array", "minItems": 2},
                    "weapon_prep_sec": {"type": "number"},
                    "flight_time_sec": {"type": "number"},
                },
            }
        },
        "additionalProperties": False,
    },
    "execution_control_planner": {
        "title": "Execution Control Planner Input",
        "type": "object",
        "required": ["phase", "results"],
        "properties": {
            "phase": {"type": "string", "enum": ["strike", "assault"]},
            "results": {"type": "object"},
        },
        "additionalProperties": True,
    },
    "mission_feature_adapter": {
        "title": "Mission Feature Adapter Input",
        "type": "object",
        "required": ["source_type"],
        "properties": {
            "source_type": {"type": "string", "enum": ["sc2le_proxy", "agent_results"]},
            "mode": {"type": "string", "enum": ["strict", "fixture", "hybrid", "test"]},
            "sc2le_proxy": {"type": "object"},
            "agent_results": {"type": "object"},
        },
        "additionalProperties": True,
    },
    "mission_completion_scorer": {
        "title": "Mission Completion Scorer Input",
        "type": "object",
        "required": ["features"],
        "properties": {
            "features": {
                "type": "object",
                "required": [
                    "damage_rate",
                    "asset_readiness",
                    "control_timeliness",
                    "intel_confidence",
                    "threat_pressure",
                    "ammo_pressure",
                    "comm_quality",
                ],
                "properties": {
                    "damage_rate": {"type": "number", "minimum": 0, "maximum": 1},
                    "asset_readiness": {"type": "number", "minimum": 0, "maximum": 1},
                    "control_timeliness": {"type": "number", "minimum": 0, "maximum": 1},
                    "intel_confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "threat_pressure": {"type": "number", "minimum": 0, "maximum": 1},
                    "ammo_pressure": {"type": "number", "minimum": 0, "maximum": 1},
                    "comm_quality": {"type": "number", "minimum": 0, "maximum": 1},
                },
            }
        },
        "additionalProperties": False,
    },
    "closed_loop_decision_advisor": {
        "title": "Closed Loop Decision Advisor Input",
        "type": "object",
        "required": ["target", "damage_probability", "situation", "mission_completion"],
        "properties": {
            "target": {"type": "object"},
            "damage_probability": {"type": "number", "minimum": 0, "maximum": 1},
            "situation": {"type": "string"},
            "mission_completion": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "additionalProperties": False,
    },
}

OUTPUT_SCHEMAS = {
    "execution_rule_matcher": {
        "title": "Execution Rule Matcher Output",
        "type": "object",
        "required": ["matched_rules", "primary_rule", "matched_items"],
        "properties": {
            "matched_rules": {"type": "array"},
            "primary_rule": {"type": "object"},
            "matched_items": {"type": "array"},
        },
    },
    "trajectory_linear_predictor": {
        "title": "Trajectory Linear Predictor Output",
        "type": "object",
        "required": ["velocity", "aim_point", "execute_at"],
        "properties": {
            "velocity": {"type": "object"},
            "aim_point": {"type": "object"},
            "execute_at": {"type": "number"},
            "future_t": {"type": "number"},
            "model": {"type": "string"},
        },
    },
    "execution_control_planner": {
        "title": "Execution Control Planner Output",
        "type": "object",
        "required": ["commands", "tracks", "coordination", "matched_rules", "prediction_details"],
        "properties": {
            "commands": {"type": "array"},
            "tracks": {"type": "array"},
            "coordination": {"type": "object"},
            "matched_rules": {"type": "array"},
            "prediction_details": {"type": "array"},
            "latency_ms": {"type": "number"},
            "phase": {"type": "string"},
        },
    },
    "mission_feature_adapter": {
        "title": "Mission Feature Adapter Output",
        "type": "object",
        "required": ["feature_version", "values", "sources", "warnings", "assessment_status"],
        "properties": {
            "feature_version": {"type": "string"},
            "values": {"type": "object"},
            "sources": {"type": "object"},
            "warnings": {"type": "array"},
            "assessment_status": {"type": "string"},
            "missing_fields": {"type": "array"},
        },
    },
    "mission_completion_scorer": {
        "title": "Mission Completion Scorer Output",
        "type": "object",
        "required": [
            "mission_completion",
            "mission_result",
            "threshold",
            "model_source",
            "feature_version",
            "assessment_status",
            "warnings",
        ],
        "properties": {
            "mission_completion": {"type": "number"},
            "mission_result": {"type": "string"},
            "threshold": {"type": "number"},
            "model_source": {"type": "string"},
            "feature_version": {"type": "string"},
            "assessment_status": {"type": "string"},
            "warnings": {"type": "array"},
        },
    },
    "closed_loop_decision_advisor": {
        "title": "Closed Loop Decision Advisor Output",
        "type": "object",
        "required": ["action", "effect_delta", "recommendation"],
        "properties": {
            "action": {"type": "string"},
            "effect_delta": {"type": "number"},
            "recommendation": {"type": "string"},
            "target_id": {"type": "string"},
        },
    },
}

GOLDEN_INPUTS = {
    "execution_rule_matcher": {
        "phase": "strike",
        "situation": {
            "threat_score": 0.75,
            "intel_confidence": 0.82,
            "resource_readiness": 0.81,
            "communication_quality": 0.9,
        },
    },
    "trajectory_linear_predictor": {
        "track": {
            "track_id": "T-001",
            "history": [
                {"t": 0.0, "x": 10.0, "y": 18.0},
                {"t": 0.1, "x": 10.4, "y": 18.6},
                {"t": 0.2, "x": 10.9, "y": 19.1},
                {"t": 0.3, "x": 11.3, "y": 19.7},
                {"t": 0.4, "x": 11.8, "y": 20.2},
            ],
            "weapon_prep_sec": 2.0,
            "flight_time_sec": 4.0,
        }
    },
    "execution_control_planner": {
        "phase": "strike",
        "results": {
            "threat_evaluation": {"output_data": {"priority_score": 0.75}},
            "perception_detection": {"output_data": {"detections": [{"conf": 0.9}]}},
            "resource_allocation": {"output_data": {"readiness": 0.85}},
            "communication": {"output_data": {"delivery_rate": 0.9}},
            "data_fusion": {
                "output_data": {
                    "track_history": [
                        {
                            "track_id": "T-001",
                            "history": [
                                {"t": 0.0, "x": 10.0, "y": 18.0},
                                {"t": 0.4, "x": 11.8, "y": 20.2},
                            ],
                            "weapon_prep_sec": 2.0,
                            "flight_time_sec": 4.0,
                        }
                    ]
                }
            },
        },
    },
    "mission_feature_adapter": {
        "source_type": "agent_results",
        "mode": "fixture",
        "agent_results": {
            "damage_confirmation": {"output_data": {"engaged_targets": 40, "confirmed_destroyed": 30}},
            "resource_allocation": {"output_data": {"readiness": 0.75, "supply_pressure": 0.55}},
            "execution_control": {"output_data": {"latency_ms": 300}},
            "perception_detection": {"output_data": {"detections": [{"conf": 0.9}, {"conf": 0.8}]}},
            "threat_evaluation": {"output_data": {"ranked_targets": [{"score": 0.7}, {"score": 0.6}]}},
            "communication": {"output_data": {"delivery_rate": 0.92}},
        },
    },
    "mission_completion_scorer": {
        "features": {
            "damage_rate": 0.7,
            "asset_readiness": 0.8,
            "control_timeliness": 0.85,
            "intel_confidence": 0.9,
            "threat_pressure": 0.6,
            "ammo_pressure": 0.4,
            "comm_quality": 0.92,
        }
    },
    "closed_loop_decision_advisor": {
        "target": {"target_id": "TGT-001", "threat_score": 0.78, "uncertainty": 0.2},
        "damage_probability": 0.62,
        "situation": "critical",
        "mission_completion": 0.72,
    },
}


def predict_outputs(algorithm_id: str, inputs: dict) -> dict:
    if algorithm_id == "execution_rule_matcher":
        phase = str(inputs["phase"])
        situation = dict(inputs["situation"])
        items = discretize_situation(situation, phase)
        rules = load_or_mine_rules()
        matched = match_rules(items, rules, phase=phase)
        primary = choose_primary_rule(matched, default_executor_role="artillery" if phase == "strike" else "assault")
        return {"matched_rules": matched, "primary_rule": primary, "matched_items": sorted(items)}
    if algorithm_id == "trajectory_linear_predictor":
        result = predict_single_track(inputs["track"])
        if not result.get("ok"):
            raise RuntimeError(result["error"]["message"])
        return {
            "velocity": result["velocity"],
            "aim_point": result["aim_point"],
            "execute_at": result["execute_at"],
            "future_t": result["future_t"],
            "model": result["model"],
            "track_id": result.get("track_id"),
        }
    if algorithm_id == "execution_control_planner":
        payload = run_planner({"phase": inputs["phase"], "results": inputs["results"]})
        output = payload["output_data"]
        return {
            "phase": output["phase"],
            "commands": output["commands"],
            "tracks": output["tracks"],
            "coordination": output["coordination"],
            "matched_rules": output["matched_rules"],
            "prediction_details": output["prediction_details"],
            "latency_ms": output["latency_ms"],
        }
    if algorithm_id == "mission_feature_adapter":
        source_type = inputs["source_type"]
        mode = str(inputs.get("mode") or "strict")
        if source_type == "sc2le_proxy":
            proxy = inputs.get("sc2le_proxy") or {}
            bundle = build_features_from_sc2le_proxy(
                mmr=float(proxy.get("mmr") or 3000.0),
                apm=float(proxy.get("apm") or 120.0),
                duration_sec=float(proxy.get("duration_sec") or 0.0),
                opponent_mmr=float(proxy.get("opponent_mmr") or 3000.0),
                result=str(proxy.get("result") or ""),
            )
        else:
            bundle = build_features_from_agent_results(inputs.get("agent_results") or {}, mode=mode)
        return {
            "feature_version": bundle["feature_version"],
            "values": bundle["values"],
            "sources": bundle.get("sources") or {},
            "warnings": bundle.get("warnings") or [],
            "assessment_status": bundle.get("assessment_status", "ready"),
            "missing_fields": bundle.get("missing_fields") or [],
        }
    if algorithm_id == "mission_completion_scorer":
        bundle = {
            "feature_version": "mission_features_v2",
            "values": inputs["features"],
            "warnings": [],
            "assessment_status": "ready",
        }
        return score_mission(bundle)
    if algorithm_id == "closed_loop_decision_advisor":
        return advise(
            inputs["target"],
            float(inputs["damage_probability"]),
            str(inputs["situation"]),
            float(inputs["mission_completion"]),
        )
    raise KeyError(algorithm_id)


def write_service_main(meta: dict) -> None:
    algorithm_id = meta["algorithm_id"]
    port = meta["port"]
    service_dir = SERVICES / algorithm_id / "app"
    service_dir.mkdir(parents=True, exist_ok=True)
    main_py = f'''#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services"))

from a2a_algorithms_common.http_service import create_algorithm_app
from a2a_algorithms_common.service_predictors import predict_{algorithm_id}, mission_model_loaded

ALGORITHM_ID = "{algorithm_id}"
VERSION = "1.0.0"
PORT = int(os.environ.get("PORT", "{port}"))


def _predict(inputs: dict, params: dict) -> dict:
    return predict_{algorithm_id}(inputs, params)


_model_loaded = mission_model_loaded if "{algorithm_id}" == "mission_completion_scorer" else (lambda: True)

app = create_algorithm_app(
    ALGORITHM_ID,
    VERSION,
    "{meta["task_family"]}",
    _predict,
    model_loaded_callable=_model_loaded,
)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=PORT)
'''
    (service_dir / "main.py").write_text(main_py, encoding="utf-8")
    readme = f"""# {meta['display_name']} Service

Run:

```bash
pip install -r services/requirements.txt
python services/{algorithm_id}/app/main.py
```

Default port: {port}
"""
    (SERVICES / algorithm_id / "README.md").write_text(readme, encoding="utf-8")


def write_example_package(meta: dict, outputs: dict) -> None:
    algorithm_id = meta["algorithm_id"]
    port = meta["port"]
    pkg = ROOT / "examples" / algorithm_id / "1.0.0"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "input.schema.json").write_text(json.dumps(INPUT_SCHEMAS[algorithm_id], ensure_ascii=False, indent=2), encoding="utf-8")
    (pkg / "output.schema.json").write_text(json.dumps(OUTPUT_SCHEMAS[algorithm_id], ensure_ascii=False, indent=2), encoding="utf-8")

    card = f"""algorithm_id: {algorithm_id}
version: 1.0.0
display_name: {meta['display_name']}
backend_type: python_http_service
status: draft

task_family: {meta['task_family']}
modalities:
  input:
    - structured_json
  output:
    - structured_json

capabilities:
  - {algorithm_id}

agent_card:
  summary: >
    {meta['display_name']} packaged for the A2A algorithm library.
  when_to_use:
    - Structured JSON inputs are available for this algorithm family.
  when_not_to_use:
    - Raw media or unstructured text without preprocessing.
  input_description: >
    See input.schema.json for required structured fields.
  output_description: >
    See output.schema.json for structured outputs.

machine_spec:
  input_schema_ref: input.schema.json
  output_schema_ref: output.schema.json
  runtime:
    backend_type: python_http_service
    endpoint: http://127.0.0.1:{port}/predict
    health_endpoint: http://127.0.0.1:{port}/health
    metadata_endpoint: http://127.0.0.1:{port}/metadata
    timeout_ms: 3000

constraints:
  max_input_chars: 10000
  max_request_bytes: 1048576
  batch_supported: false
  streaming_supported: false

performance:
  latency_ms_p50: 20
  latency_ms_p95: 100
  primary_metric: {meta['primary_metric']}
  primary_score: {meta['primary_score']}

safety:
  risk_level: {meta['risk_level']}
  requires_human_review: true
"""
    (pkg / "algorithm_card.yaml").write_text(card, encoding="utf-8")
    (pkg / "README.md").write_text(
        f"# {meta['display_name']}\n\nPython HTTP Service algorithm package for `{algorithm_id}`.\n",
        encoding="utf-8",
    )
    (pkg / "service_contract.md").write_text(
        f"# Service Contract\n\n- GET /health\n- GET /metadata\n- POST /predict\n\nPort: {port}\n",
        encoding="utf-8",
    )

    request = {
        "request_id": "req_001",
        "trace_id": "trace_001",
        "algorithm_id": algorithm_id,
        "version": "1.0.0",
        "backend_type": "python_http_service",
        "inputs": GOLDEN_INPUTS[algorithm_id],
        "params": {},
    }
    response = {
        "ok": True,
        "request_id": "req_001",
        "trace_id": "trace_001",
        "algorithm_id": algorithm_id,
        "version": "1.0.0",
        "outputs": outputs,
        "usage": {"latency_ms": 1},
        "error": None,
    }
    golden = pkg / "golden_cases"
    golden.mkdir(parents=True, exist_ok=True)
    (golden / "case_001_request.json").write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
    (golden / "case_001_response.json").write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    for meta in ALGORITHMS:
        algorithm_id = meta["algorithm_id"]
        outputs = predict_outputs(algorithm_id, GOLDEN_INPUTS[algorithm_id])
        write_example_package(meta, outputs)
        write_service_main(meta)
        print(f"bootstrapped {algorithm_id}")


if __name__ == "__main__":
    main()
