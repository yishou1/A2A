# Service Contract

- `GET /health` reports the implementation readiness and `model_loaded=true`.
- `GET /metadata` reports M01 identity and the supported implementations.
- `POST /predict` accepts the standard A2A algorithm request envelope.

Default endpoint: `http://127.0.0.1:9031/predict`.
