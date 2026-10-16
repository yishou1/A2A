# SynapseRAG bundled service

## Ownership and migration

For standalone algorithm-library delivery, use
[SYNAPSE_STANDALONE_DELIVERY.md](../../docs/SYNAPSE_STANDALONE_DELIVERY.md).
The instructions below describe the original A2A development launcher.

This directory is a source copy of the independent SynapseRAG working tree,
including uncommitted tracing and source-preview work, imported on 2026-09-14.
The original repository is preserved. Develop the A2A-integrated service here;
changes are not automatically synchronized back to the original repository.
The original LICENSE is retained. This is not a Git submodule.

The delivery knowledge base is committed under
`algorithmrepo/knowledge_bases/newport_roe_handbook_2022/`. It contains the
source PDF, parsed chunks, OpenIE output, vector indexes, knowledge graph,
indexing task database, and retrieval trace database. Stored document paths are
repository-relative, so previews and PDF highlighting do not depend on the
original SynapseRAG checkout.

Qwen and embedding weights are not bundled. The deployment machine provides
compatible HTTP model services. Secrets, virtual environments, model weights,
caches, and logs remain outside Git.

## Start in WSL

From the A2A repository root:

```bash
bash scripts/start_synapserag.sh
```

The foreground process stops with Ctrl+C. Port 8000 must be free; do not run the
old and new services against the same mutable data directory at the same time.
This launcher does not start Ollama, AMOS, Commander or AlgoLib.

The launcher loads `.runtime/synapserag/service.env` (ignored by Git), or the
explicit file supplied through `SYNAPSERAG_ENV_FILE`. Without local overrides it
uses `python3`, the bundled knowledge base, and index ID
`newport-roe-handbook-2022-v1`. The launcher pins module lookup to this source
copy. Model servers remain external. Install service dependencies in the
selected Python environment and edit model endpoints using `service.env.example`.
The legacy requirements and setup.py have differing dependency pins; they are
not a validated lockfile for reproducing the current environment.

New indexing jobs and retrieval traces are written to the bundled `records/`
directory by default and are intentionally tracked. Stop the service before
committing database updates so SQLite can checkpoint temporary WAL files.
Configure OpenIE credentials separately for new document indexing.

## HTTP integration

For both Agents and their core algorithm service processes:

```bash
export RAG_BACKEND=synapserag
export SYNAPSERAG_BASE_URL=http://127.0.0.1:8000
# Set matching SYNAPSERAG_API_TOKEN on both sides if authentication is enabled.
```

The existing client continues to call `POST /api/retrieve`. Document ingestion,
retrieval traces, graph overlays and source previews remain HTTP endpoints on
this independent service. The standalone launcher now registers
`synapse_rag_retriever:2.0.0` and `synapse_graph_explorer:1.0.0`.
The TIA version 1.0.0 remains available. AMOS reads the same trace, graph, and
evidence endpoints through its read-only proxy and can open the bundled PDF at
the highlighted evidence location.

```bash
curl --fail http://127.0.0.1:8000/api/health
curl --fail http://127.0.0.1:8000/openapi.json
```

For an isolated test, copy the bundled directory to a temporary location and use
a different port. A health response alone does not prove that model endpoints
are ready.
