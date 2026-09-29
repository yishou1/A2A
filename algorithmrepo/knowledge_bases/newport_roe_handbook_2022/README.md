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

The committed prebuilt index is
`indexes/newport-roe-handbook-2022-v1/qwen3-embedding_0.6b/`. It uses
`qwen3-embedding:0.6b` with 1024-dimensional vectors and contains the graph,
entity, fact, and chunk indexes required at service startup. The artifacts use
portable source mappings and do not contain development-machine absolute paths.
On a clean Docker start, the image contents initialize the `synapserag_data`
named volume, so users do not need to run a separate indexing job.

Qwen and embedding model weights are deliberately not included. The customer
provides OpenAI-compatible model endpoints through the `SYNAPSERAG_QA_*` and
`SYNAPSERAG_EMBEDDING_*` environment variables.

New task and trace records are written to `records/` by default. Commit those
database changes when a new delivery snapshot is intended.
