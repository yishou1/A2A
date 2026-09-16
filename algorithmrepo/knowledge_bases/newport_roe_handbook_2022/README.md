# Newport ROE Handbook Knowledge Base

This directory is a self-contained SynapseRAG delivery fixture. It is committed
with the algorithm library so retrieval, graph exploration, source preview, and
the AMOS explainability demonstration do not depend on the original development
machine.

Contents:

- `sources/`: original source documents used by evidence preview and highlighting.
- `documents/`: parsed document and chunk artifacts.
- `openie/`: extracted entities, relations, and facts.
- `indexes/`: vector indexes and the knowledge graph.
- `records/jobs.sqlite3`: committed indexing task history.
- `records/retrieval_traces.sqlite3`: committed retrieval traces used by the UI.
- `source_manifest.json` and `source_chunks.json`: portable source mappings.

Qwen and embedding model weights are deliberately not included. The customer
provides OpenAI-compatible model endpoints through the `SYNAPSERAG_QA_*` and
`SYNAPSERAG_EMBEDDING_*` environment variables.

New task and trace records are written to `records/` by default. Commit those
database changes when a new delivery snapshot is intended.
