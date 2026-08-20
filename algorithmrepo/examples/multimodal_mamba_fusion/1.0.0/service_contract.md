# Service Contract

- GET /health
- GET /metadata
- POST /predict

Port: 9026

Real mode verifies the checkpoint SHA256 and embedding dimension before health
readiness. Predictions return the input dimensions and model identity alongside
unit-normalized fused embeddings.
