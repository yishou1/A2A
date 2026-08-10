# compliance_authorization_core

Python HTTP Service algorithm package for the A2A compliance and authorization
agent's structured-rule, SynapseRAG evidence, and ONNX calibration pipeline.

The service reads `RAG_BACKEND`, `SYNAPSERAG_BASE_URL`,
`SYNAPSERAG_API_TOKEN`, `SYNAPSERAG_TIMEOUT_SECONDS`, and
`SYNAPSERAG_TOP_K_COMPLIANCE` from its environment. Retrieval failure keeps
blocking decisions and forces all other outcomes to human review.

It checks candidate plans against structured rules, authorization state, and constraints.

Port: `9021`
