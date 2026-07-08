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

Output includes candidate plans, recommended plan id, plan scores, target trends, `model_runtime`, and non-RAG warnings.

The service loads `models/decision_planning_lr.onnx` for candidate-plan scoring. It also loads
`models/decision_planning_lstm.onnx` when a target has 12 history steps; shorter histories fall
back to the Python formula and are marked in `model_runtime`.

Port: `9020`
