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

Reference PDFs remain outside the source tree. Their originals are still under
the original repository's docs directory; copies made during migration are in
`/home/dell/project_bupt/synapserag-migration-reference-documents/`. Older example
commands mentioning `docs/*.pdf` need an external absolute document path.

The copy excludes Git history, secrets, virtual environments, outputs, downloaded
models, dataset JSON files and caches. Existing dependencies and data remain
outside A2A. Do not delete the original directory while it still hosts them.

## Start in WSL

From the A2A repository root:

```bash
bash scripts/start_synapserag.sh
```

The foreground process stops with Ctrl+C. Port 8000 must be free; do not run the
old and new services against the same mutable data directory at the same time.
This launcher does not start Ollama, AMOS, Commander or AlgoLib.

The launcher loads `.runtime/synapserag/service.env` (ignored by Git), or the
explicit file supplied through `SYNAPSERAG_ENV_FILE`. On this machine it reuses:

- Python: `/home/dell/project_bupt/SynapseRAG/.venv/bin/python`
- Data: `/home/dell/project_bupt/SynapseRAG/outputs/newport_roe_handbook_2022`
- Index ID: `newport-roe-handbook-2022-v1`

The external environment is reused without copying or reinstalling it. The
launcher pins module lookup to this source copy. Model servers remain external.
On another machine, create a dedicated environment outside A2A, install and
validate its dependencies, and edit the paths using `service.env.example`.
The legacy requirements and setup.py have differing dependency pins; they are
not a validated lockfile for reproducing the current environment.

Existing index manifests can contain absolute document paths. Keeping data in
place preserves source previews. Moving it later requires checking manifests,
the active-index pointer and original-document paths, not just changing SAVE_DIR.
Configure OpenIE model credentials separately for new document indexing; they
are deliberately not copied from the original `.env`.

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
The TIA version 1.0.0 remains available. The AMOS
trace sidebar integration is not restored by this migration.

```bash
curl --fail http://127.0.0.1:8000/api/health
curl --fail http://127.0.0.1:8000/openapi.json
```

For an isolated test, use a temporary external SAVE_DIR and a different port;
do not run tests against production index or trace databases. A health response
alone does not prove that an index or the model endpoints are ready.
