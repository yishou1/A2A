# Service Contract

- `GET /health` returns ready only when the model artifact SHA256 is valid.
- `GET /metadata` reports M05 random-forest identity.
- `POST /predict` accepts the standard A2A algorithm request envelope.

Default endpoint: `http://127.0.0.1:9032/predict`.
