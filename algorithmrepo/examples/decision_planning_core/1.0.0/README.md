# decision_planning_core

Python HTTP Service orchestrator for candidate generation, independent model
capabilities, structured rules, and optional SynapseRAG evidence.

Model capabilities are versioned algorithm packages rather than private core
assets:

- `decision_plan_recommender_onnx:1.0.0`
- `target_trend_predictor_onnx:1.0.0`

If an ONNX package or runtime is unavailable, the capability adapter records
the reason in `model_runtime` and uses the deterministic formula implementation.

The service reads `RAG_BACKEND`, `SYNAPSERAG_BASE_URL`,
`SYNAPSERAG_API_TOKEN`, `SYNAPSERAG_TIMEOUT_SECONDS`, and
`SYNAPSERAG_TOP_K_PLANNING` from its environment. Retrieval failure preserves
deterministic rule scoring and records a warning.

It generates and ranks candidate plans from structured tasks, resources, risks, and constraints.

Port: `9036`
