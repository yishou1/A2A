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

Output includes compliance decision, selected plan id, authorization status,
violations with evidence IDs, risk probability, `model_runtime`,
`rag_evidence`, and `rag_duration_ms`.

The core composes `compliance_risk_scorer_onnx:1.0.0` for risk calibration.
The model lives in its own algorithm package. If the package or runtime is
unavailable, the adapter uses the deterministic formula and records the reason
in `model_runtime`.

Port: `9037`
