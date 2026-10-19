# Service Contract

- `GET /health` becomes ready only after model SHA256 verification.
- `GET /metadata` reports the M07 GaussianNB identity.
- `POST /predict` accepts the standard A2A algorithm request envelope.

Default endpoint: `http://127.0.0.1:9033/predict`.
