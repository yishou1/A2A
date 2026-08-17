# Service Contract

- GET /health
- GET /metadata
- POST /predict

Port: 9024

In real mode, `/health` reports `model_loaded=true` only after the checkpoint
SHA256 and network dimensions match the metadata. `/predict` returns the
checkpoint identity in `outputs.model`.
