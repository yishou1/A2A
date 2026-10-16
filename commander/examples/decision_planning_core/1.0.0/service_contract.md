# Decision Planning Core service contract

## GET /health

Returns service readiness for `decision_planning_core`.

## GET /metadata

Returns algorithm metadata, including `backend_type=python_http_service`.

## POST /predict

Request envelope:

```json
{
  "algorithm_id": "decision_planning_core",
  "version": "1.0.0",
  "inputs": {
    "task_id": "TASK-001",
    "objective": "Generate candidate plans",
    "scheduled_tasks": [],
    "resources": [],
    "constraints": {}
  },
  "params": {}
}
```

Output includes candidate plans, recommended plan id, rule-adjusted plan scores,
target trends, `model_runtime`, `rag_evidence`, and `rag_duration_ms`.

The core composes `decision_plan_recommender_onnx:1.0.0` for candidate-plan
scoring and `target_trend_predictor_onnx:1.0.0` for 12-step target histories.
The models live in their own algorithm packages. Short histories and unavailable
capability packages use deterministic formula fallbacks recorded in `model_runtime`.

Port: `9036`
