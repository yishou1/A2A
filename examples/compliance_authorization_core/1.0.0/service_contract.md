# Compliance Authorization Core service contract

## GET /health

Returns service readiness for `compliance_authorization_core`.

## GET /metadata

Returns algorithm metadata, including `backend_type=python_http_service`.

## POST /predict

Request envelope:

```json
{
  "algorithm_id": "compliance_authorization_core",
  "version": "1.0.0",
  "inputs": {
    "task_id": "TASK-001",
    "objective": "Check candidate plan compliance",
    "candidate_plans": [],
    "authorization": {},
    "constraints": {}
  },
  "params": {}
}
```

Output includes compliance decision, selected plan id, authorization status, violations, risk probability, `model_runtime`, and non-RAG warnings.

The service loads `models/compliance_authorization_lr.onnx` for compliance risk calibration. If
the model is unavailable, it falls back to the Python formula and records the reason in
`model_runtime`.

Port: `9021`
