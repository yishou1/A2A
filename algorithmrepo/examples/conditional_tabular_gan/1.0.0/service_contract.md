# Service Contract

- `GET /health` verifies the trained generator, source, and dataset hashes.
- `GET /metadata` reports M08 conditional-GAN identity.
- `POST /predict` generates seeded synthetic feature rows.

Default endpoint: `http://127.0.0.1:9035/predict`.
