# decision_planning_core

Python HTTP Service algorithm package for the A2A decision planning agent's
ONNX, structured-rule, and optional SynapseRAG pipeline.

The service reads `RAG_BACKEND`, `SYNAPSERAG_BASE_URL`,
`SYNAPSERAG_API_TOKEN`, `SYNAPSERAG_TIMEOUT_SECONDS`, and
`SYNAPSERAG_TOP_K_PLANNING` from its environment. Retrieval failure preserves
deterministic rule scoring and records a warning.

It generates and ranks candidate plans from structured tasks, resources, risks, and constraints.

Port: `9036`
