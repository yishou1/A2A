# Service Contract

- GET /health
- GET /metadata
- POST /predict

Port: 9022

Real-mode `/health` reports `model_loaded=true` only after checkpoint SHA256 and
network dimensions match the metadata. `POST /predict` returns every candidate,
its decision evidence, explicit partitions, summary counts, and model identity.

`manual_review` decisions are not included in `verified_detections`; a human
reviewer must resolve them using the source observation and returned evidence.
