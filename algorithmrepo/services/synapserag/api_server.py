"""Document ingestion and query service around the existing SynapseRAG core."""

import asyncio
import hashlib
import json
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Literal, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator

from src.synapserag import SynapseRAG
from src.synapserag.document_ingestion import chunk_document, parse_document
from src.synapserag.document_ingestion.io import save_document_artifacts
from src.synapserag.document_ingestion.source_preview import (
    SourcePreviewError,
    find_document,
    highlighted_preview,
    locate_chunk,
)
from src.synapserag.tracing import (
    RetrievalTraceCollector,
    RetrievalTraceStore,
    build_graph_overlays,
    select_graph_overlay,
)
from src.synapserag.utils.config_utils import BaseConfig, LLMEndpointConfig
from src.synapserag.utils.misc_utils import compute_mdhash_id


ALGORITHM_REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_KNOWLEDGE_ROOT = (
    ALGORITHM_REPO_ROOT / "knowledge_bases" / "newport_roe_handbook_2022"
)
SAVE_DIR = os.getenv("SYNAPSERAG_SAVE_DIR", str(BUNDLED_KNOWLEDGE_ROOT))
INDEX_ID = os.getenv("SYNAPSERAG_INDEX_ID", "newport-roe-handbook-2022-v1")
EMBEDDING_MODEL = os.getenv("SYNAPSERAG_EMBEDDING_MODEL", "qwen3-embedding:0.6b")
EMBEDDING_BASE_URL = os.getenv("SYNAPSERAG_EMBEDDING_BASE_URL", "http://127.0.0.1:11434/v1")
QA_MODEL = os.getenv("SYNAPSERAG_QA_MODEL", "qwen3:1.7b")
QA_BASE_URL = os.getenv("SYNAPSERAG_QA_BASE_URL", "http://127.0.0.1:11434/v1")
OPENIE_PROMPT_VERSION = "ner-triple-cn-v2"
SUPPORTED_EXTENSIONS = {".txt", ".text", ".md", ".markdown", ".pdf", ".docx"}
DATA_ROOT = Path(SAVE_DIR).expanduser().resolve()
RECORDS_ROOT = Path(
    os.getenv("SYNAPSERAG_RECORDS_DIR", str(DATA_ROOT / "records"))
).expanduser().resolve()
JOB_DB_PATH = RECORDS_ROOT / "jobs.sqlite3"
TRACE_DB_PATH = RECORDS_ROOT / "retrieval_traces.sqlite3"
ACTIVE_POINTER = DATA_ROOT / "active.json"
_state_lock = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _db() -> sqlite3.Connection:
    JOB_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(JOB_DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def init_job_db() -> None:
    """Create/migrate the durable job table without discarding old records."""
    with _db() as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                job_type TEXT NOT NULL DEFAULT 'ingest',
                status TEXT NOT NULL,
                progress INTEGER NOT NULL,
                message TEXT NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                result_json TEXT,
                payload_json TEXT,
                attempt INTEGER NOT NULL DEFAULT 0
            )""")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
        migrations = {
            "job_type": "ALTER TABLE jobs ADD COLUMN job_type TEXT NOT NULL DEFAULT 'ingest'",
            "payload_json": "ALTER TABLE jobs ADD COLUMN payload_json TEXT",
            "attempt": "ALTER TABLE jobs ADD COLUMN attempt INTEGER NOT NULL DEFAULT 0",
        }
        for column, statement in migrations.items():
            if column not in columns:
                connection.execute(statement)


def create_job(payload: dict, *, job_id: Optional[str] = None, job_type: str = "ingest") -> str:
    job_id = job_id or uuid.uuid4().hex
    now = _now()
    document_count = len(payload.get("documents", []))
    with _db() as connection:
        connection.execute(
            """INSERT INTO jobs
               (job_id, job_type, status, progress, message, error, created_at,
                updated_at, result_json, payload_json, attempt)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (job_id, job_type, "queued", 0, f"queued {document_count} document(s)",
             None, now, now, None, json.dumps(payload, ensure_ascii=False), 0),
        )
    return job_id


def _get_job_record(job_id: str) -> Optional[dict]:
    with _db() as connection:
        row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    return dict(row) if row is not None else None


def get_job(job_id: str) -> Optional[dict]:
    record = _get_job_record(job_id)
    if record is None:
        return None
    result_json = record.pop("result_json", None)
    record.pop("payload_json", None)
    record["result"] = json.loads(result_json) if result_json else None
    return record


def update_job(job_id: str, *, status: str, progress: int, message: str,
               error: Optional[str] = None, result: Optional[dict] = None) -> None:
    with _db() as connection:
        connection.execute(
            """UPDATE jobs SET status=?, progress=?, message=?, error=?,
               updated_at=?, result_json=? WHERE job_id=?""",
            (status, max(0, min(100, progress)), message, error, _now(),
             json.dumps(result, ensure_ascii=False) if result is not None else None, job_id),
        )


def _claim_job(job_id: str) -> bool:
    """Atomically claim a queued job, including with multiple server processes."""
    with _db() as connection:
        cursor = connection.execute(
            """UPDATE jobs SET status='parsing', progress=10,
               message='parsing documents', updated_at=?
               WHERE job_id=? AND status='queued'""",
            (_now(), job_id),
        )
    return cursor.rowcount == 1


def recover_incomplete_jobs() -> List[str]:
    states = ("queued", "parsing", "openie", "building", "validating")
    with _db() as connection:
        rows = connection.execute(
            f"SELECT job_id FROM jobs WHERE status IN ({','.join('?' for _ in states)})",
            states,
        ).fetchall()
        job_ids = [row[0] for row in rows]
        if job_ids:
            connection.executemany(
                """UPDATE jobs SET status='queued', progress=0,
                   message='recovered after service restart', updated_at=? WHERE job_id=?""",
                [(_now(), job_id) for job_id in job_ids],
            )
    return job_ids


def reset_failed_job(job_id: str) -> bool:
    with _db() as connection:
        cursor = connection.execute(
            """UPDATE jobs SET status='queued', progress=0, message='queued for retry',
               error=NULL, result_json=NULL, updated_at=?, attempt=attempt+1
               WHERE job_id=? AND status='failed'""",
            (_now(), job_id),
        )
    return cursor.rowcount == 1


def qa_endpoint() -> LLMEndpointConfig:
    return LLMEndpointConfig(
        model_name=QA_MODEL,
        base_url=QA_BASE_URL,
        api_key_env="SYNAPSERAG_QA_API_KEY",
        temperature=0.1,
        max_tokens=1024,
        extra_body={"reasoning_effort": "none"},
    )


def make_config(stage: str, *, include_openie: bool = False,
                save_dir: Optional[str] = None, index_id: Optional[str] = None) -> BaseConfig:
    openie_endpoint = None
    if include_openie:
        provider = os.getenv("SYNAPSERAG_OPENIE_PROVIDER", os.getenv("LLM_PROVIDER", "openai_compatible"))
        openie_base = os.getenv("SYNAPSERAG_OPENIE_BASE_URL") or os.getenv("TOOL_LLM_URL")
        is_azure = provider.lower() == "azure"
        openie_api_version = (
            os.getenv("SYNAPSERAG_OPENIE_API_VERSION") or os.getenv("AZURE_OPENAI_API_VERSION")
            if is_azure else None
        )
        openie_endpoint = LLMEndpointConfig(
            model_name=os.getenv("SYNAPSERAG_OPENIE_MODEL") or os.getenv("TOOL_LLM_NAME", "qwen-plus"),
            provider=provider,
            base_url=None if is_azure else openie_base,
            azure_endpoint=openie_base if is_azure else None,
            api_version=openie_api_version,
            api_key_env=os.getenv("SYNAPSERAG_OPENIE_API_KEY_ENV", "API_KEY" if is_azure else "SYNAPSERAG_OPENIE_API_KEY"),
            timeout_seconds=int(os.getenv("LLM_TIMEOUT_SECONDS", "120")),
            temperature=0.0,
            max_tokens=2048,
        )
    return BaseConfig(
        save_dir=save_dir or SAVE_DIR,
        index_id=index_id or INDEX_ID,
        runtime_stage=stage,
        openie_prompt_version=OPENIE_PROMPT_VERSION,
        openie_llm=openie_endpoint,
        qa_llm=qa_endpoint(),
        embedding_model_name=EMBEDDING_MODEL,
        embedding_base_url=EMBEDDING_BASE_URL,
        fact_rerank_mode="hybrid",
        fact_candidate_top_k=20,
        fact_rerank_top_k=5,
        linking_top_k=5,
        retrieval_top_k=8,
        retrieval_fusion_mode="hybrid",
        qa_top_k=5,
        use_agentic_ppr_reset=True,
    )


def _active_pointer() -> dict:
    return _read_json(ACTIVE_POINTER, {}) or {}


def _resolve_data_path(value: object, *roots: Path) -> Path:
    path = Path(str(value or ""))
    if path.is_absolute():
        return path.resolve()
    candidates = [(root / path).resolve() for root in roots]
    return next((candidate for candidate in candidates if candidate.exists()), candidates[0])


def _delivery_path(path: Path) -> str:
    """Store paths relative to the delivered knowledge-base root when possible."""
    resolved = path.expanduser().resolve()
    try:
        return resolved.relative_to(DATA_ROOT).as_posix()
    except ValueError:
        return str(resolved)


def _active_save_dir() -> str:
    configured = _active_pointer().get("save_dir")
    return str(_resolve_data_path(configured, DATA_ROOT)) if configured else str(DATA_ROOT)


def _normalize_manifest_paths(manifest: dict, root: Path) -> dict:
    """Resolve repository-relative delivery paths without changing saved JSON."""
    for item in manifest.get("documents", []):
        configured_dir = item.get("artifact_dir") or item.get("output_dir")
        candidates = []
        if configured_dir:
            candidates.append(_resolve_data_path(configured_dir, root, DATA_ROOT))
        candidates.append((root / "documents" / str(item.get("document_id") or "")).resolve())
        output_dir = next(
            (candidate for candidate in candidates if (candidate / "parsed_document.json").is_file()),
            candidates[-1],
        )
        parsed = _read_json(output_dir / "parsed_document.json", {}) or {}
        source_value = item.get("upload_path") or parsed.get("source_path")
        if source_value:
            source_path = _resolve_data_path(source_value, root, DATA_ROOT, output_dir)
            if source_path.is_file():
                item["upload_path"] = str(source_path)
        item["artifact_dir"] = str(output_dir)
        item.setdefault("content_sha256", parsed.get("content_sha256"))
    return manifest


def _active_manifest() -> dict:
    pointer = _active_pointer()
    manifest_path = pointer.get("service_manifest")
    if manifest_path:
        resolved_manifest = _resolve_data_path(manifest_path, DATA_ROOT)
        manifest = _read_json(resolved_manifest, {}) or {}
        return _normalize_manifest_paths(manifest, Path(_active_save_dir()))
    legacy = _read_json(DATA_ROOT / "source_manifest.json", {}) or {}
    return _normalize_manifest_paths(legacy, DATA_ROOT)


def _load_source_map() -> Dict[str, List[dict]]:
    pointer = _active_pointer()
    source_path = pointer.get("source_map")
    if source_path:
        return _read_json(_resolve_data_path(source_path, DATA_ROOT), {}) or {}
    # Backward compatibility with the first service prototype.
    legacy = _read_json(DATA_ROOT / "source_chunks.json", {}) or {}
    return {text: value if isinstance(value, list) else [value] for text, value in legacy.items()}


def load_query_runtime() -> SynapseRAG:
    rag = SynapseRAG(global_config=make_config("query", save_dir=_active_save_dir()))
    rag.load_index()
    return rag


def _index_version(rag, manifest: dict) -> str:
    if manifest.get("job_id"):
        return str(manifest["job_id"])
    path = getattr(rag, "index_manifest_path", None)
    if path and Path(path).is_file():
        return "manifest-" + hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return "unversioned"


def _logical_document_id(filename: str) -> str:
    normalized = filename.strip().casefold()
    return "doc-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _safe_document_id(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", value):
        return value
    return "doc-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _copy_compatible_openie_cache(
    version_dir: Path,
    openie_config: BaseConfig,
    texts: List[str],
) -> bool:
    target = version_dir / "openie" / INDEX_ID / "openie_results.json"
    if target.exists():
        # A failed job retry keeps its successful chunk cache, but the active
        # document snapshot may have changed while the job was failed.
        source = target
    else:
        active_dir = _active_pointer().get("save_dir")
        seed_path = os.getenv("SYNAPSERAG_OPENIE_CACHE_SEED")
        if active_dir:
            source = Path(active_dir) / "openie" / INDEX_ID / "openie_results.json"
        elif seed_path:
            source = Path(seed_path)
        else:
            return False
    cached = _read_json(source, {}) or {}
    endpoint = openie_config.get_llm_endpoint("openie")
    if (cached.get("source_model") != endpoint.model_name
            or cached.get("prompt_version") != OPENIE_PROMPT_VERSION):
        return False
    requested_keys = {compute_mdhash_id(text, prefix="chunk-") for text in texts}
    cached["docs"] = [
        item for item in cached.get("docs", [])
        if compute_mdhash_id(item.get("passage", ""), prefix="chunk-") in requested_keys
    ]
    cached["index_id"] = INDEX_ID
    target.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(target, cached)
    return True


def _build_document_snapshot(payload: dict) -> Dict[str, dict]:
    current = {
        item["document_id"]: item
        for item in _active_manifest().get("documents", [])
        if item.get("document_id") and item.get("upload_path")
    }
    for document_id in payload.get("remove_document_ids", []):
        current.pop(document_id, None)
    for item in payload.get("documents", []):
        normalized = dict(item)
        normalized["upload_path"] = str(_resolve_data_path(item["upload_path"], DATA_ROOT))
        current[item["document_id"]] = normalized
    return current


def _run_ingest_job(job_id: str) -> None:
    if not _claim_job(job_id):
        return
    record = _get_job_record(job_id)
    try:
        if not record or not record.get("payload_json"):
            raise ValueError("job payload is missing and cannot be recovered")
        payload = json.loads(record["payload_json"])
        document_snapshot = _build_document_snapshot(payload)
        if not document_snapshot:
            raise ValueError("the knowledge base cannot be activated without documents")

        all_chunks = []
        source_map: Dict[str, List[dict]] = {}
        version_documents = []
        for document_id, item in sorted(document_snapshot.items()):
            document = parse_document(item["upload_path"], document_id=document_id)
            # The preserved file name contains a collision-safe prefix; expose
            # the original client filename in artifacts and citations.
            document.filename = item["filename"]
            chunks = chunk_document(document)
            artifact_dir = DATA_ROOT / "documents" / document_id / document.content_sha256
            report = save_document_artifacts(document, chunks, artifact_dir)
            parsed_path = artifact_dir / "parsed_document.json"
            parsed_payload = _read_json(parsed_path, {}) or {}
            parsed_payload["source_path"] = _delivery_path(Path(item["upload_path"]))
            _write_json_atomic(parsed_path, parsed_payload)
            version_item = dict(item)
            version_item.update({
                "content_sha256": document.content_sha256,
                "parser": document.parser,
                "chunk_count": len(chunks),
                "upload_path": _delivery_path(Path(item["upload_path"])),
                "artifact_dir": _delivery_path(artifact_dir),
                "updated_at": _now(),
            })
            version_documents.append(version_item)
            all_chunks.extend(chunks)
            for chunk in chunks:
                source_map.setdefault(chunk.text, []).append({
                    "chunk_id": chunk.chunk_id,
                    "document_id": chunk.document_id,
                    "filename": chunk.source_filename,
                    "page_start": chunk.page_start,
                    "page_end": chunk.page_end,
                    "heading_path": chunk.heading_path,
                    "source_segments": chunk.source_segments,
                })

        texts = list(dict.fromkeys(chunk.text for chunk in all_chunks))
        version_dir = DATA_ROOT / "versions" / job_id
        openie_config = make_config("openie", include_openie=True, save_dir=str(version_dir))
        reused_cache = _copy_compatible_openie_cache(version_dir, openie_config, texts)
        update_job(job_id, status="openie", progress=30, message="extracting missing OpenIE chunks")
        openie_rag = SynapseRAG(global_config=openie_config)
        openie_report = openie_rag.extract_openie(texts, force=False)

        update_job(job_id, status="building", progress=60, message="building embedding and graph index")
        # A retry may have a partially written index.  Only the failed job's
        # shadow index is removed; its successful OpenIE cache is retained.
        shutil.rmtree(version_dir / "indexes", ignore_errors=True)
        build_rag = SynapseRAG(global_config=make_config("build-index", save_dir=str(version_dir)))
        index_report = build_rag.build_index(texts, allow_openie_calls=False)
        update_job(job_id, status="validating", progress=85, message="validating new index")
        query_rag = SynapseRAG(global_config=make_config("query", save_dir=str(version_dir)))
        query_rag.load_index()

        source_path = version_dir / "source_chunks.json"
        manifest_path = version_dir / "service_manifest.json"
        _write_json_atomic(source_path, source_map)
        service_manifest = {
            "schema_version": 2,
            "job_id": job_id,
            "index_id": INDEX_ID,
            "save_dir": _delivery_path(version_dir),
            "documents": version_documents,
            "chunk_count": len(texts),
            "created_at": _now(),
        }
        _write_json_atomic(manifest_path, service_manifest)
        pointer = {
            "schema_version": 2,
            "job_id": job_id,
            "save_dir": _delivery_path(version_dir),
            "service_manifest": _delivery_path(manifest_path),
            "source_map": _delivery_path(source_path),
            "activated_at": _now(),
        }
        with _state_lock:
            _write_json_atomic(ACTIVE_POINTER, pointer)
            app.state.rag = query_rag
            app.state.source_map = source_map
            app.state.active_manifest = _normalize_manifest_paths(
                json.loads(json.dumps(service_manifest)), version_dir
            )
            app.state.startup_error = None
        result = {
            "documents": [
                {key: item.get(key) for key in ("document_id", "filename", "content_sha256", "parser", "chunk_count")}
                for item in version_documents
            ],
            "chunk_count": len(texts),
            "openie_cache_reused": reused_cache,
            "openie": openie_report,
            "index": index_report,
        }
        update_job(job_id, status="succeeded", progress=100, message="index is active", result=result)
    except Exception as error:
        update_job(job_id, status="failed", progress=100, message="ingestion failed", error=str(error))


def _submit_job(app_instance: FastAPI, job_id: str):
    return app_instance.state.executor.submit(_run_ingest_job, job_id)


@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    init_job_db()
    app_instance.state.trace_store = RetrievalTraceStore(TRACE_DB_PATH)
    app_instance.state.trace_store.initialize()
    app_instance.state.trace_store.purge_older_than(
        int(os.getenv("SYNAPSERAG_TRACE_RETENTION_DAYS", "0"))
    )
    app_instance.state.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="synapserag-index")
    app_instance.state.query_lock = asyncio.Lock()
    app_instance.state.source_map = _load_source_map()
    app_instance.state.active_manifest = _active_manifest()
    try:
        app_instance.state.rag = load_query_runtime()
        app_instance.state.startup_error = None
    except Exception as error:
        app_instance.state.rag = None
        app_instance.state.startup_error = str(error)
    for job_id in recover_incomplete_jobs():
        _submit_job(app_instance, job_id)
    try:
        yield
    finally:
        # Running jobs remain recoverable in SQLite.  Do not make HTTP service
        # shutdown wait indefinitely for a remote OpenIE request.
        app_instance.state.executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(
    title="SynapseRAG API Server",
    description="文档解析、OpenIE、Qwen3 embedding、图检索与本地问答服务",
    lifespan=lifespan,
)


class DocumentItem(BaseModel):
    idx: str
    title: str
    text: str


class IndexRequest(BaseModel):
    docs: List[DocumentItem]


class QueryRequest(BaseModel):
    query: str


class RetrieveQuery(BaseModel):
    query_id: str = Field(min_length=1, max_length=256)
    text: str = Field(min_length=1, max_length=4000)


class RetrievalContext(BaseModel):
    workflow_id: Optional[str] = Field(default=None, min_length=1, max_length=256)
    task_id: Optional[str] = Field(default=None, min_length=1, max_length=256)
    work_item_id: Optional[str] = Field(default=None, min_length=1, max_length=256)
    agent_id: Optional[str] = Field(default=None, min_length=1, max_length=256)


class GraphExploreRequest(BaseModel):
    model_config = {"extra": "forbid"}
    operation: Literal["neighbors", "paths", "trace"]
    index_id: Optional[str] = Field(default=None, min_length=1, max_length=256)
    entity_id: Optional[str] = Field(default=None, min_length=1, max_length=256)
    source_id: Optional[str] = Field(default=None, min_length=1, max_length=256)
    target_id: Optional[str] = Field(default=None, min_length=1, max_length=256)
    trace_id: Optional[str] = Field(default=None, min_length=1, max_length=256)
    query_trace_id: Optional[str] = Field(default=None, min_length=1, max_length=256)
    evidence_node_key: Optional[str] = Field(default=None, min_length=1, max_length=256)
    view: Literal["skeleton", "candidates"] = "skeleton"
    max_hops: int = Field(default=2, ge=1, le=6)
    max_nodes: int = Field(default=50, ge=1, le=200)
    max_edges: int = Field(default=80, ge=0, le=400)
    max_paths: int = Field(default=3, ge=1, le=3)

    @model_validator(mode="after")
    def required_arguments(self):
        required = {"neighbors": ["entity_id"], "paths": ["source_id", "target_id"],
                    "trace": ["trace_id"]}[self.operation]
        if any(not getattr(self, name) for name in required):
            raise ValueError("required for operation: " + ", ".join(required))
        return self


class RetrievalExplainOptions(BaseModel):
    enabled: bool = False
    level: Literal["summary", "detailed"] = "summary"


class RetrieveRequest(BaseModel):
    model_config = {"extra": "forbid"}
    schema_version: Literal["1.0"] = "1.0"
    index_id: Optional[str] = Field(default=None, min_length=1, max_length=256)
    request_id: str = Field(min_length=1, max_length=256)
    purpose: Literal["planning", "compliance", "general"] = "general"
    top_k: int = Field(default=3, ge=1, le=10)
    context: Optional[RetrievalContext] = None
    explain: RetrievalExplainOptions = Field(default_factory=RetrievalExplainOptions)
    queries: List[RetrieveQuery] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_queries(self):
        ids = [query.query_id for query in self.queries]
        if len(ids) != len(set(ids)):
            raise ValueError("query_id values must be unique")
        return self


def _require_retrieve_token(request: Request) -> None:
    expected = os.getenv("SYNAPSERAG_API_TOKEN", "").strip()
    if not expected:
        return
    authorization = request.headers.get("authorization", "")
    scheme, _, supplied = authorization.partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=401,
            detail={"error_code": "AUTHENTICATION_ERROR", "message": "invalid bearer token"},
            headers={"WWW-Authenticate": "Bearer"},
        )


def _raise_source_error(error: SourcePreviewError) -> None:
    raise HTTPException(
        status_code=error.status_code,
        detail={"error_code": error.code, "message": str(error)},
    ) from error


def _citation(origin: dict) -> str:
    source = str(origin.get("filename") or origin.get("document_id") or "unknown-source")
    page_start = origin.get("page_start")
    page_end = origin.get("page_end")
    if page_start is None:
        return source
    if page_end is not None and page_end != page_start:
        return f"{source} pp.{page_start}-{page_end}"
    return f"{source} p.{page_start}"


def _retrieval_evidence(text: str, score: Optional[float], rank: int, origin: dict) -> dict:
    heading_path = origin.get("heading_path") or []
    if isinstance(heading_path, str):
        heading_path = [heading_path]
    section = " > ".join(str(item) for item in heading_path if item)
    source = str(origin.get("filename") or origin.get("document_id") or "unknown-source")
    return {
        "chunk_id": str(origin.get("chunk_id") or compute_mdhash_id(text, prefix="chunk-")),
        "node_key": compute_mdhash_id(text, prefix="chunk-"),
        "document_id": str(origin.get("document_id") or ""),
        "source": source,
        "title": section or source,
        "text": text,
        "score": score,
        "rank": rank,
        "page_start": origin.get("page_start"),
        "page_end": origin.get("page_end"),
        "section": section or None,
        "citation": _citation(origin),
        "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


@app.get("/api/health")
async def health(request: Request):
    indexed = request.app.state.rag is not None
    startup_error = getattr(request.app.state, "startup_error", None)
    return {
        "status": "ok" if indexed or not startup_error else "degraded",
        "indexed": indexed,
        "startup_error": startup_error,
        "active_job_id": _active_pointer().get("job_id"),
    }


@app.post("/api/index")
async def build_index(request: Request, payload: IndexRequest):
    """Backward-compatible JSON indexing, serialized through the durable queue."""
    job_id = uuid.uuid4().hex
    upload_dir = DATA_ROOT / "uploads" / job_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    documents = []
    for position, doc in enumerate(payload.docs):
        document_id = _safe_document_id(doc.idx)
        path = upload_dir / f"{position:05d}-{document_id}.txt"
        content = f"{doc.title}\n{doc.text}".encode("utf-8")
        path.write_bytes(content)
        documents.append({
            "document_id": document_id,
            "filename": doc.title or path.name,
            "upload_path": _delivery_path(path),
            "content_sha256": hashlib.sha256(content).hexdigest(),
            "uploaded_at": _now(),
        })
    create_job({"documents": documents, "remove_document_ids": []}, job_id=job_id, job_type="legacy-index")
    future = _submit_job(request.app, job_id)
    while not future.done():
        await asyncio.sleep(0.02)
    future.result()
    job = get_job(job_id)
    if not job or job["status"] != "succeeded":
        raise HTTPException(status_code=500, detail=(job or {}).get("error", "index job failed"))
    return {"status": "success", "job_id": job_id, "report": job["result"]}


@app.post("/api/documents/upload", status_code=202)
async def upload_documents(request: Request):
    """Validate all multipart files, preserve them, then enqueue one upsert job."""
    try:
        form = await request.form()
    except (AssertionError, RuntimeError, ImportError) as error:
        raise HTTPException(status_code=501, detail="multipart upload requires python-multipart; install project requirements") from error
    items = list(form.multi_items())
    files = [item for _, item in items if hasattr(item, "filename") and item.filename]
    requested_ids = [str(item) for key, item in items if key in {"document_id", "document_ids"} and not hasattr(item, "filename")]
    if not files:
        raise HTTPException(status_code=400, detail="no supported files were uploaded")
    if requested_ids and len(requested_ids) != len(files):
        raise HTTPException(status_code=400, detail="document_id count must match uploaded file count")

    filenames = [Path(item.filename).name for item in files]
    for filename in filenames:
        if Path(filename).suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise HTTPException(status_code=415, detail=f"unsupported file format: {filename}")
    document_ids = [
        _safe_document_id(requested_ids[index]) if requested_ids else _logical_document_id(filename)
        for index, filename in enumerate(filenames)
    ]
    if len(set(document_ids)) != len(document_ids):
        raise HTTPException(status_code=409, detail="duplicate document IDs in one upload request")

    job_id = uuid.uuid4().hex
    upload_dir = DATA_ROOT / "uploads" / job_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    max_bytes = int(os.getenv("SYNAPSERAG_MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))
    max_total_bytes = int(os.getenv("SYNAPSERAG_MAX_REQUEST_BYTES", str(100 * 1024 * 1024)))
    total_bytes = 0
    documents = []
    try:
        for index, (upload, filename, document_id) in enumerate(zip(files, filenames, document_ids)):
            destination = upload_dir / f"{index:05d}-{filename}"
            digest = hashlib.sha256()
            file_bytes = 0
            with destination.open("wb") as stream:
                while True:
                    block = await upload.read(1024 * 1024)
                    if not block:
                        break
                    file_bytes += len(block)
                    total_bytes += len(block)
                    if file_bytes > max_bytes or total_bytes > max_total_bytes:
                        raise HTTPException(status_code=413, detail=f"upload limit exceeded: {filename}")
                    digest.update(block)
                    stream.write(block)
            documents.append({
                "document_id": document_id,
                "filename": filename,
                "upload_path": _delivery_path(destination),
                "content_sha256": digest.hexdigest(),
                "uploaded_at": _now(),
            })
            await upload.close()
    except Exception:
        for upload in files:
            await upload.close()
        shutil.rmtree(upload_dir, ignore_errors=True)
        raise

    create_job({"documents": documents, "remove_document_ids": []}, job_id=job_id)
    _submit_job(request.app, job_id)
    return {"job_id": job_id, "status": "queued", "documents": [
        {"document_id": item["document_id"], "filename": item["filename"]} for item in documents
    ]}


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@app.post("/api/jobs/{job_id}/retry", status_code=202)
async def retry_job(request: Request, job_id: str):
    if _get_job_record(job_id) is None:
        raise HTTPException(status_code=404, detail="job not found")
    if not reset_failed_job(job_id):
        raise HTTPException(status_code=409, detail="only failed jobs can be retried")
    _submit_job(request.app, job_id)
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/documents")
async def list_documents(request: Request):
    documents = []
    for item in request.app.state.active_manifest.get("documents", []):
        documents.append({key: item.get(key) for key in (
            "document_id", "filename", "content_sha256", "parser", "chunk_count", "updated_at"
        )})
    return {"documents": documents}


@app.get("/api/documents/{document_id}/original")
async def get_original_document(request: Request, document_id: str):
    """Return an indexed source file without accepting a filesystem path."""
    _require_retrieve_token(request)
    with _state_lock:
        manifest = dict(request.app.state.active_manifest)
    try:
        document = find_document(manifest, document_id)
    except SourcePreviewError as error:
        _raise_source_error(error)
    path = Path(document["resolved_path"])
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    filename = re.sub(r'[^A-Za-z0-9._-]+', '_', str(document.get("filename") or path.name))
    return Response(
        await asyncio.to_thread(path.read_bytes),
        media_type=media_type,
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "private, max-age=300",
        },
    )


@app.get("/api/evidence/{chunk_id}/location")
async def get_evidence_location(request: Request, chunk_id: str):
    """Resolve a retrieved chunk to a source page or character span."""
    _require_retrieve_token(request)
    with _state_lock:
        source_map = {
            text: list(origins) for text, origins in request.app.state.source_map.items()
        }
        manifest = dict(request.app.state.active_manifest)
    try:
        location = await asyncio.to_thread(locate_chunk, source_map, manifest, chunk_id)
    except SourcePreviewError as error:
        _raise_source_error(error)
    location["preview_url"] = f"/api/evidence/{chunk_id}/preview"
    location["original_url"] = f"/api/documents/{location['document_id']}/original"
    return location


@app.get("/api/evidence/{chunk_id}/preview")
async def get_evidence_preview(request: Request, chunk_id: str):
    """Return a highlighted PDF copy or a safe highlighted HTML document."""
    _require_retrieve_token(request)
    with _state_lock:
        source_map = {
            text: list(origins) for text, origins in request.app.state.source_map.items()
        }
        manifest = dict(request.app.state.active_manifest)
    try:
        payload, media_type, filename = await asyncio.to_thread(
            highlighted_preview, source_map, manifest, chunk_id
        )
    except SourcePreviewError as error:
        _raise_source_error(error)
    safe_filename = re.sub(r'[^A-Za-z0-9._-]+', '_', filename) or "evidence-preview"
    return Response(
        payload,
        media_type=media_type,
        headers={
            "Content-Disposition": f'inline; filename="{safe_filename}"',
            "Cache-Control": "private, max-age=300",
        },
    )


@app.post("/api/retrieve")
async def retrieve_evidence(request: Request, payload: RetrieveRequest):
    """Return source-attributed chunks without invoking the QA model."""
    _require_retrieve_token(request)
    with _state_lock:
        rag = request.app.state.rag
        source_map = {
            text: list(origins)
            for text, origins in request.app.state.source_map.items()
        }
        manifest = dict(request.app.state.active_manifest)
    if rag is None:
        raise HTTPException(
            status_code=503,
            detail={
                "error_code": "INDEX_NOT_READY",
                "message": "no active index; upload documents or call /api/index first",
            },
        )

    if payload.index_id and payload.index_id != str(manifest.get("index_id") or INDEX_ID):
        raise HTTPException(status_code=409, detail={
            "error_code": "INDEX_MISMATCH", "message": "requested index is not active"
        })

    started = time.perf_counter()
    trace_persisted = False
    trace_enabled = payload.explain.enabled and os.getenv(
        "SYNAPSERAG_TRACE_ENABLED", "true"
    ).strip().lower() not in {"0", "false", "no", "off"}
    collector = None
    if trace_enabled:
        collector = RetrievalTraceCollector(
            request_id=payload.request_id,
            purpose=payload.purpose,
            query_ids=[item.query_id for item in payload.queries],
            context=payload.context.model_dump(exclude_none=True) if payload.context else {},
            level=payload.explain.level,
            index_id=str(manifest.get("index_id") or INDEX_ID),
            index_version=_index_version(rag, manifest),
            max_channel_results=int(os.getenv("SYNAPSERAG_TRACE_MAX_CHANNEL_RESULTS", "20")),
            max_ppr_nodes=int(os.getenv("SYNAPSERAG_TRACE_MAX_PPR_NODES", "30")),
            max_overlay_nodes=int(os.getenv("SYNAPSERAG_TRACE_MAX_NODES", "25")),
            max_overlay_edges=int(os.getenv("SYNAPSERAG_TRACE_MAX_EDGES", "20")),
        )
    try:
        retrieve_kwargs = {"num_to_retrieve": payload.top_k}
        if collector is not None:
            retrieve_kwargs["trace_collector"] = collector
        async with request.app.state.query_lock:
            solutions = await asyncio.to_thread(
                rag.retrieve,
                [item.text for item in payload.queries],
                **retrieve_kwargs,
            )
        if len(solutions) != len(payload.queries):
            raise RuntimeError(
                "retrieval result count does not match the requested query count"
            )
        results = []
        for query_position, (query, solution) in enumerate(zip(payload.queries, solutions)):
            evidence = []
            for rank, text in enumerate(solution.docs[: payload.top_k], start=1):
                score = None
                if solution.doc_scores is not None and rank - 1 < len(solution.doc_scores):
                    score = float(solution.doc_scores[rank - 1])
                origins = source_map.get(text) or [{}]
                evidence.extend(
                    _retrieval_evidence(text, score, rank, origin)
                    for origin in origins
                )
            result = {"query_id": query.query_id, "evidence": evidence}
            if collector is not None:
                collector.record_evidence(query_position, evidence)
                if query_position < len(collector.queries):
                    result["query_trace_id"] = collector.queries[query_position]["query_trace_id"]
            results.append(result)
        if collector is not None:
            try:
                build_graph_overlays(
                    rag,
                    collector.queries,
                    max_nodes=collector.max_overlay_nodes,
                    max_edges=collector.max_overlay_edges,
                    max_paths=int(os.getenv("SYNAPSERAG_TRACE_MAX_PATHS", "3")),
                    max_hops=int(os.getenv("SYNAPSERAG_TRACE_MAX_HOPS", "4")),
                )
            except Exception as error:
                collector.warnings.append(f"graph_overlay_failed:{error}")
            collector.complete()
            try:
                request.app.state.trace_store.save(collector.to_dict())
                trace_persisted = True
            except Exception as error:
                collector.warnings.append(f"trace_persistence_failed:{error}")
        duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
        response = {
            "schema_version": "1.0",
            "status": "success",
            "request_id": payload.request_id,
            "results": results,
            "retrieval_profile": {
                "backend": "synapserag",
                "index_id": manifest.get("index_id") or INDEX_ID,
                "index_version": _index_version(rag, manifest),
                "duration_ms": duration_ms,
            },
            "warnings": list(collector.warnings) if collector is not None else [],
        }
        if collector is not None and trace_persisted:
            response["trace_id"] = collector.trace_id
        return response
    except HTTPException:
        raise
    except Exception as error:
        trace_id = None
        if collector is not None:
            collector.fail("RETRIEVAL_ERROR", str(error))
            try:
                request.app.state.trace_store.save(collector.to_dict())
                trace_id = collector.trace_id
            except Exception:
                pass
        detail = {"error_code": "RETRIEVAL_ERROR", "message": str(error)}
        if trace_id:
            detail["trace_id"] = trace_id
        raise HTTPException(
            status_code=500,
            detail=detail,
        ) from error


@app.get("/api/retrieval-traces")
async def list_retrieval_traces(
    request: Request,
    task_id: Optional[str] = None,
    workflow_id: Optional[str] = None,
    request_id: Optional[str] = None,
    limit: int = 50,
):
    """List compact trace summaries for task and workflow navigation."""
    _require_retrieve_token(request)
    traces = request.app.state.trace_store.list(
        task_id=task_id,
        workflow_id=workflow_id,
        request_id=request_id,
        limit=limit,
    )
    return {"status": "success", "count": len(traces), "traces": traces}


@app.get("/api/retrieval-traces/{trace_id}")
async def get_retrieval_trace(request: Request, trace_id: str):
    """Return the complete bounded retrieval trace and attributed evidence."""
    _require_retrieve_token(request)
    trace = request.app.state.trace_store.get(trace_id)
    if trace is None:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "TRACE_NOT_FOUND", "message": f"trace not found: {trace_id}"},
        )
    return trace


@app.get("/api/retrieval-traces/{trace_id}/graph")
async def get_retrieval_trace_graph(
    request: Request,
    trace_id: str,
    query_trace_id: Optional[str] = None,
    view: Literal["skeleton", "candidates"] = "skeleton",
    evidence_node_key: Optional[str] = None,
    max_paths: int = Query(default=1, ge=1, le=3),
):
    """Return a compact evidence path or a node-only candidate overlay."""
    _require_retrieve_token(request)
    trace = request.app.state.trace_store.get(trace_id)
    if trace is None:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "TRACE_NOT_FOUND", "message": f"trace not found: {trace_id}"},
        )
    return {
        "trace_schema_version": trace.get("trace_schema_version", "1.0"),
        "trace_id": trace_id,
        "task_id": trace.get("task_id"),
        "workflow_id": trace.get("workflow_id"),
        "index_id": trace.get("index_id"),
        "index_version": trace.get("index_version"),
        "queries": [
            {
                "query_trace_id": query.get("query_trace_id"),
                "query_id": query.get("query_id"),
                "stage_summary": query.get("stage_summary", []),
                "display_stats": query.get("display_stats", {}),
                "evidence_paths": query.get("evidence_paths", []),
                "graph_overlay": select_graph_overlay(
                    query,
                    view=view,
                    evidence_node_key=evidence_node_key,
                    max_paths=max_paths,
                ),
            }
            for query in trace.get("queries", [])
            if not query_trace_id or query.get("query_trace_id") == query_trace_id
        ],
    }


@app.post("/api/graph/explore")
async def explore_graph(request: Request, payload: GraphExploreRequest):
    from src.synapserag.tracing.explorer import explore

    _require_retrieve_token(request)
    started = time.perf_counter()
    if payload.operation == "trace":
        trace = request.app.state.trace_store.get(payload.trace_id)
        if trace is None:
            raise HTTPException(404, detail={"error_code": "TRACE_NOT_FOUND", "message": "trace not found"})
        if payload.index_id and payload.index_id != trace.get("index_id"):
            raise HTTPException(409, detail={"error_code": "INDEX_MISMATCH", "message": "trace belongs to another index"})
        queries = [q for q in trace.get("queries", [])
                   if not payload.query_trace_id or q.get("query_trace_id") == payload.query_trace_id]
        if payload.query_trace_id and not queries:
            raise HTTPException(404, detail={"error_code": "TRACE_QUERY_NOT_FOUND", "message": "query not found"})
        nodes, edges, paths = {}, {}, []
        truncated = False
        for query in queries:
            overlay = select_graph_overlay(query, view=payload.view,
                                           evidence_node_key=payload.evidence_node_key,
                                           max_paths=payload.max_paths)
            for node in overlay.get("nodes", []):
                if len(nodes) < payload.max_nodes:
                    nodes[node["node_key"]] = node
                elif node["node_key"] not in nodes:
                    truncated = True
            for edge in overlay.get("edges", []):
                key = (edge["source_key"], edge["target_key"])
                if all(k in nodes for k in key) and len(edges) < payload.max_edges:
                    edges[key] = edge
                else:
                    truncated = True
            for path in query.get("evidence_paths", []):
                if payload.evidence_node_key and path.get("evidence_node_key") != payload.evidence_node_key:
                    continue
                if (payload.view == "skeleton" and len(paths) < payload.max_paths
                        and all(k in nodes for k in path.get("nodes", []))
                        and all((e["source_key"], e["target_key"]) in edges for e in path.get("edges", []))):
                    paths.append(path)
            truncated = truncated or overlay.get("truncated", False)
        result = {"nodes": list(nodes.values()), "edges": list(edges.values()), "paths": paths,
                  "truncated": truncated, "trace_id": payload.trace_id,
                  "index_id": trace.get("index_id"), "index_version": trace.get("index_version"),
                  "semantics": "persisted_retrieval_explanation"}
    else:
        with _state_lock:
            rag = request.app.state.rag
            manifest = dict(request.app.state.active_manifest)
            source_map = dict(request.app.state.source_map)
        if rag is None:
            raise HTTPException(503, detail={"error_code": "INDEX_NOT_READY", "message": "no active index"})
        index_id = str(manifest.get("index_id") or INDEX_ID)
        if payload.index_id and payload.index_id != index_id:
            raise HTTPException(409, detail={"error_code": "INDEX_MISMATCH", "message": "requested index is not active"})
        try:
            async with request.app.state.query_lock:
                result = await asyncio.to_thread(explore, rag, payload, source_map)
        except KeyError as error:
            raise HTTPException(404, detail={"error_code": "ENTITY_NOT_FOUND", "message": str(error)}) from error
        result.update(index_id=index_id, index_version=_index_version(rag, manifest))
    result.update(status="success", duration_ms=round((time.perf_counter()-started)*1000, 3))
    return result


@app.post("/api/query")
async def query_rag(request: Request, payload: QueryRequest):
    with _state_lock:
        rag = request.app.state.rag
        source_map = {text: list(origins) for text, origins in request.app.state.source_map.items()}
    if rag is None:
        raise HTTPException(status_code=503, detail="no active index; upload documents or call /api/index first")
    try:
        async with request.app.state.query_lock:
            solutions, raw_responses, metadata = await asyncio.to_thread(rag.rag_qa, [payload.query])
        solution = solutions[0]
        sources = []
        for rank, text in enumerate(solution.docs, start=1):
            score = None
            if solution.doc_scores is not None and rank - 1 < len(solution.doc_scores):
                score = float(solution.doc_scores[rank - 1])
            origins = source_map.get(text) or [{}]
            for origin in origins:
                source = dict(origin)
                source.update({"chunk_text": text, "score": score, "rank": rank})
                sources.append(source)
        return {
            "query": payload.query,
            "answer": solution.answer,
            "raw_response": raw_responses[0] if raw_responses else "",
            "retrieved_context": solution.docs,
            "sources": sources,
            "metadata": metadata[0] if metadata else {},
        }
    except Exception as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


# uvicorn api_server:app --host 0.0.0.0 --port 8000 --workers 1
