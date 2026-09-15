# compliance_authorization_core

Python HTTP Service orchestrator for structured-rule checks, SynapseRAG
evidence, and compliance-risk calibration.

Risk calibration is provided by the independent
`compliance_risk_scorer_onnx:1.0.0` algorithm package. If that package or the
ONNX runtime is unavailable, the adapter records the reason in `model_runtime`
and uses the deterministic formula implementation.

The service reads `RAG_BACKEND`, `SYNAPSERAG_BASE_URL`,
`SYNAPSERAG_API_TOKEN`, `SYNAPSERAG_TIMEOUT_SECONDS`, and
`SYNAPSERAG_TOP_K_COMPLIANCE` from its environment. Retrieval failure keeps
blocking decisions and forces all other outcomes to human review.

It checks candidate plans against structured rules, authorization state, and constraints.

Port: `9037`
