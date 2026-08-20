# Service Contract

- GET /health
- GET /metadata
- POST /predict

Port: 9011

`/health` reports `model_loaded=true` only when the implementation and reference
evaluation dataset hashes match `models/trajectory_linear_predictor.metadata.json`.
