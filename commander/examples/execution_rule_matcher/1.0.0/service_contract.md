# Service Contract

- GET /health
- GET /metadata
- POST /predict

Port: 9010

`/health` reports `model_loaded=true` only when the persisted mined-rules file
matches the SHA256 recorded in `models/execution_rule_matcher.metadata.json`.
