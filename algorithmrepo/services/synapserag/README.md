# SynapseRAG HTTP Service

Independent document ingestion, graph-based retrieval and evidence tracing
service bundled with A2A. Python dependencies, model servers and persistent data
remain outside this source directory.

## Run

From the A2A repository root:

```bash
bash scripts/start_synapserag.sh
```

Configure external Python and data paths in
`A2A/.runtime/synapserag/service.env`. See [deployment](DEPLOYMENT_A2A.md)
and [configuration example](service.env.example). The launcher does not start
Ollama or other A2A services.

## Interfaces

- `GET /api/health`: service and index readiness.
- `POST /api/documents/upload`: document ingestion.
- `POST /api/retrieve`: retrieval without QA generation.
- `POST /api/query`: retrieval and question answering.
- `GET /api/retrieval-traces`: trace summaries.
- `GET /api/retrieval-traces/{trace_id}`: trace detail.
- `GET /api/retrieval-traces/{trace_id}/graph`: evidence graph overlay.
- `GET /api/evidence/{chunk_id}/location`: original-document location.
- `GET /api/evidence/{chunk_id}/preview`: highlighted source preview.

See [retrieval API reference](API_REFERENCE.md) and the running service's
`/docs` or `/openapi.json` for the complete interface schema.

## Layout

- `api_server.py`: FastAPI entry point.
- `src/synapserag/`: retrieval, ingestion and tracing implementation.
- `tests/`: regression tests and small fixtures.
- `scripts/`: ingestion, migration, diagnostics and demo utilities.
- `requirements.txt`, `setup.py`: dependency and package declarations.

Historical experiment and demo Python scripts are retained, but they are not
required by the HTTP service. Research notes, weekly reports and old experiment
documentation are not included. The original source repository remains intact.

License: [MIT](LICENSE).
