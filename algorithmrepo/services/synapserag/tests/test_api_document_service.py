import asyncio
from concurrent.futures import Future
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import httpx

import api_server as service
from src.synapserag.utils.misc_utils import QuerySolution
from src.synapserag.SynapseRAG import SynapseRAG
from src.synapserag.document_ingestion.source_preview import (
    highlighted_preview,
    locate_chunk,
)
from src.synapserag.tracing import (
    RetrievalTraceCollector,
    RetrievalTraceStore,
    build_graph_overlays,
    select_graph_overlay,
)


class FakeSynapseRAG:
    built_texts = {}
    fail_next_openie = False
    fail_next_retrieve = False

    def __init__(self, global_config):
        self.global_config = global_config

    def extract_openie(self, texts, force=False):
        if type(self).fail_next_openie:
            type(self).fail_next_openie = False
            raise RuntimeError("simulated OpenIE outage")
        return {"stage": "openie", "processed_count": len(texts), "reused_count": 0}

    def build_index(self, texts, allow_openie_calls=False):
        type(self).built_texts[str(self.global_config.save_dir)] = list(texts)
        return {"stage": "build-index", "document_count": len(texts)}

    def load_index(self):
        if str(self.global_config.save_dir) not in type(self).built_texts:
            raise FileNotFoundError("no fake index")
        return {"stage": "query"}

    def rag_qa(self, queries):
        docs = type(self).built_texts[str(self.global_config.save_dir)]
        solution = QuerySolution(
            question=queries[0],
            docs=docs,
            doc_scores=np.asarray([1.0 - index * 0.1 for index in range(len(docs))]),
            answer="测试答案",
        )
        return [solution], ["raw"], [{"fake": True}]

    def retrieve(self, queries, num_to_retrieve=5, trace_collector=None):
        if type(self).fail_next_retrieve:
            type(self).fail_next_retrieve = False
            raise RuntimeError("simulated retrieval outage")
        docs = type(self).built_texts[str(self.global_config.save_dir)][:num_to_retrieve]
        solutions = []
        for position, query in enumerate(queries):
            query_trace = trace_collector.begin_query(position, query) if trace_collector else None
            solutions.append(QuerySolution(
                question=query,
                docs=docs,
                doc_scores=np.asarray([1.0 - index * 0.1 for index in range(len(docs))]),
            ))
            if query_trace is not None:
                passage_keys = [service.compute_mdhash_id(text, prefix="chunk-") for text in docs]
                document_ids = np.arange(len(docs), dtype=int)
                scores = np.asarray([1.0 - index * 0.1 for index in range(len(docs))])
                trace_collector.record_channel(
                    query_trace, "dense", document_ids, scores, passage_keys
                )
                trace_collector.finish_query(query_trace)
        return solutions

class ImmediateExecutor:
    """Deterministic executor for HTTP state-machine tests."""

    def __init__(self, *args, **kwargs):
        pass

    def submit(self, function, *args, **kwargs):
        future = Future()
        try:
            future.set_result(function(*args, **kwargs))
        except Exception as error:
            future.set_exception(error)
        return future

    def shutdown(self, *args, **kwargs):
        pass


async def immediate_to_thread(function, *args, **kwargs):
    return function(*args, **kwargs)


class APIDocumentServiceTests(unittest.TestCase):
    @staticmethod
    async def _wait_for_job(client, job_id, expected="succeeded"):
        for _ in range(250):
            payload = (await client.get(f"/api/jobs/{job_id}")).json()
            if payload["status"] in {"succeeded", "failed"}:
                if payload["status"] != expected:
                    raise AssertionError(payload)
                return payload
            await asyncio.sleep(0.02)
        raise AssertionError("job did not finish")

    def test_http_document_service_flows(self):
        asyncio.run(self._exercise_service())

    async def _exercise_service(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        patcher = patch.multiple(
            service,
            SAVE_DIR=str(root),
            DATA_ROOT=root,
            JOB_DB_PATH=root / "jobs.sqlite3",
            TRACE_DB_PATH=root / "retrieval_traces.sqlite3",
            ACTIVE_POINTER=root / "active.json",
            INDEX_ID="test-kb",
            SynapseRAG=FakeSynapseRAG,
            ThreadPoolExecutor=ImmediateExecutor,
        )
        patcher.start()
        to_thread_patcher = patch.object(service.asyncio, "to_thread", immediate_to_thread)
        to_thread_patcher.start()
        FakeSynapseRAG.built_texts = {}
        FakeSynapseRAG.fail_next_openie = False
        FakeSynapseRAG.fail_next_retrieve = False
        lifespan = service.lifespan(service.app)
        await lifespan.__aenter__()
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=service.app),
            base_url="http://testserver",
        )
        try:
            unavailable = await client.post("/api/retrieve", json={
                "request_id": "not-ready",
                "purpose": "planning",
                "queries": [{"query_id": "RULE-1", "text": "test"}],
            })
            self.assertEqual(unavailable.status_code, 503, unavailable.text)
            await self._exercise_multi_source_and_upsert(client)
            await self._exercise_retry(client)
            await self._exercise_legacy_endpoint(client)
        finally:
            await client.aclose()
            await lifespan.__aexit__(None, None, None)
            to_thread_patcher.stop()
            patcher.stop()
            temporary.cleanup()

    async def _exercise_multi_source_and_upsert(self, client):
        response = await client.post("/api/documents/upload", files=[
            ("files", ("a.txt", "共同内容".encode(), "text/plain")),
            ("files", ("b.txt", "共同内容".encode(), "text/plain")),
        ])
        self.assertEqual(response.status_code, 202, response.text)
        job = await self._wait_for_job(client, response.json()["job_id"])
        self.assertEqual(len(job["result"]["documents"]), 2)
        document_response = await client.get("/api/documents")
        self.assertEqual(len(document_response.json()["documents"]), 2)

        query = await client.post("/api/query", json={"query": "内容是什么？"})
        self.assertEqual(query.status_code, 200, query.text)
        self.assertEqual({item["filename"] for item in query.json()["sources"]}, {"a.txt", "b.txt"})

        retrieve = await client.post("/api/retrieve", json={
            "schema_version": "1.0",
            "request_id": "dp-001",
            "purpose": "planning",
            "top_k": 1,
            "queries": [{"query_id": "RULE-BLOCK-001", "text": "内容是什么？"}],
        })
        self.assertEqual(retrieve.status_code, 200, retrieve.text)
        body = retrieve.json()
        self.assertEqual(body["results"][0]["query_id"], "RULE-BLOCK-001")
        self.assertEqual({item["source"] for item in body["results"][0]["evidence"]}, {"a.txt", "b.txt"})
        for item in body["results"][0]["evidence"]:
            self.assertEqual(len(item["content_hash"]), 64)
            self.assertTrue(item["citation"])
            self.assertTrue(item["node_key"].startswith("chunk-"))

        first_evidence = body["results"][0]["evidence"][0]
        location = await client.get(f"/api/evidence/{first_evidence['chunk_id']}/location")
        self.assertEqual(location.status_code, 200, location.text)
        self.assertEqual(location.json()["precision"], "character")
        self.assertEqual(location.json()["document_id"], first_evidence["document_id"])
        preview = await client.get(f"/api/evidence/{first_evidence['chunk_id']}/preview")
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertIn("<mark>", preview.text)
        original = await client.get(
            f"/api/documents/{first_evidence['document_id']}/original"
        )
        self.assertEqual(original.status_code, 200, original.text)
        self.assertEqual(original.content, "共同内容".encode())
        missing_source = await client.get("/api/evidence/unknown-chunk/location")
        self.assertEqual(missing_source.status_code, 404)
        self.assertEqual(missing_source.json()["detail"]["error_code"], "EVIDENCE_NOT_FOUND")

        explained = await client.post("/api/retrieve", json={
            "schema_version": "1.0",
            "request_id": "explain-001",
            "purpose": "compliance",
            "top_k": 1,
            "context": {
                "workflow_id": "workflow-001",
                "task_id": "task-001",
                "work_item_id": "work-item-001",
                "agent_id": "compliance-agent",
            },
            "explain": {"enabled": True, "level": "detailed"},
            "queries": [{"query_id": "RULE-TRACE-001", "text": "内容是什么？"}],
        })
        self.assertEqual(explained.status_code, 200, explained.text)
        explained_body = explained.json()
        trace_id = explained_body["trace_id"]
        self.assertTrue(trace_id.startswith("trace-"))
        self.assertTrue(explained_body["results"][0]["query_trace_id"].startswith("qtrace-"))

        trace_list = await client.get("/api/retrieval-traces", params={"task_id": "task-001"})
        self.assertEqual(trace_list.status_code, 200, trace_list.text)
        self.assertEqual(trace_list.json()["count"], 1)
        self.assertEqual(trace_list.json()["traces"][0]["workflow_id"], "workflow-001")

        trace_detail = await client.get(f"/api/retrieval-traces/{trace_id}")
        self.assertEqual(trace_detail.status_code, 200, trace_detail.text)
        self.assertEqual(trace_detail.json()["queries"][0]["query_id"], "RULE-TRACE-001")
        self.assertEqual(len(trace_detail.json()["queries"][0]["evidence"]), 2)
        self.assertTrue(
            trace_detail.json()["queries"][0]["evidence"][0]["retrieval_reason"]["dense_recalled"]
        )

        trace_graph = await client.get(
            f"/api/retrieval-traces/{trace_id}/graph", params={"view": "candidates"}
        )
        self.assertEqual(trace_graph.status_code, 200, trace_graph.text)
        self.assertEqual(trace_graph.json()["task_id"], "task-001")
        self.assertEqual(trace_graph.json()["queries"][0]["query_id"], "RULE-TRACE-001")
        self.assertEqual(trace_graph.json()["queries"][0]["graph_overlay"]["edges"], [])
        self.assertEqual(trace_detail.json()["trace_schema_version"], "1.1")
        self.assertTrue(trace_detail.json()["queries"][0]["stage_summary"])

        invalid = await client.post("/api/retrieve", json={
            "request_id": "invalid-1",
            "purpose": "planning",
            "top_k": 11,
            "queries": [{"query_id": "RULE-1", "text": "test"}],
        })
        self.assertEqual(invalid.status_code, 422)

        FakeSynapseRAG.fail_next_retrieve = True
        failed = await client.post("/api/retrieve", json={
            "request_id": "failed-1",
            "purpose": "planning",
            "context": {"task_id": "failed-task"},
            "explain": {"enabled": True},
            "queries": [{"query_id": "RULE-1", "text": "test"}],
        })
        self.assertEqual(failed.status_code, 500, failed.text)
        self.assertEqual(failed.json()["detail"]["error_code"], "RETRIEVAL_ERROR")
        failed_trace_id = failed.json()["detail"]["trace_id"]
        failed_trace = await client.get(f"/api/retrieval-traces/{failed_trace_id}")
        self.assertEqual(failed_trace.json()["status"], "failed")
        self.assertEqual(failed_trace.json()["task_id"], "failed-task")

        with patch.dict("os.environ", {"SYNAPSERAG_API_TOKEN": "secret"}):
            unauthorized = await client.post("/api/retrieve", json={
                "request_id": "auth-1",
                "purpose": "compliance",
                "queries": [{"query_id": "RULE-1", "text": "test"}],
            })
            self.assertEqual(unauthorized.status_code, 401)
            authorized = await client.post(
                "/api/retrieve",
                headers={"Authorization": "Bearer secret"},
                json={
                    "request_id": "auth-2",
                    "purpose": "compliance",
                    "queries": [{"query_id": "RULE-1", "text": "test"}],
                },
            )
            self.assertEqual(authorized.status_code, 200, authorized.text)
            unauthorized_trace = await client.get("/api/retrieval-traces")
            self.assertEqual(unauthorized_trace.status_code, 401)
            unauthorized_source = await client.get(
                f"/api/evidence/{first_evidence['chunk_id']}/location"
            )
            self.assertEqual(unauthorized_source.status_code, 401)
            authorized_trace = await client.get(
                "/api/retrieval-traces",
                headers={"Authorization": "Bearer secret"},
            )
            self.assertEqual(authorized_trace.status_code, 200, authorized_trace.text)

        pointer = service._active_pointer()
        self.assertTrue(Path(pointer["service_manifest"]).is_file())
        self.assertTrue(Path(pointer["source_map"]).is_file())
        self.assertEqual(Path(pointer["save_dir"]), Path(pointer["service_manifest"]).parent)

        first = await client.post("/api/documents/upload", files={
            "files": ("policy.txt", "旧内容".encode(), "text/plain")
        })
        await self._wait_for_job(client, first.json()["job_id"])
        second = await client.post("/api/documents/upload", files={
            "files": ("policy.txt", "新内容".encode(), "text/plain")
        })
        await self._wait_for_job(client, second.json()["job_id"])

        documents = (await client.get("/api/documents")).json()["documents"]
        policy_documents = [item for item in documents if item["filename"] == "policy.txt"]
        self.assertEqual(len(policy_documents), 1)
        self.assertEqual(policy_documents[0]["content_sha256"], hashlib.sha256("新内容".encode()).hexdigest())
        query = (await client.post("/api/query", json={"query": "最新内容？"})).json()
        self.assertTrue(any("新内容" in text for text in query["retrieved_context"]))
        self.assertFalse(any("旧内容" in text for text in query["retrieved_context"]))

    async def _exercise_retry(self, client):
        FakeSynapseRAG.fail_next_openie = True
        response = await client.post("/api/documents/upload", files={
            "files": ("retry.txt", "可重试内容".encode(), "text/plain")
        })
        job_id = response.json()["job_id"]
        await self._wait_for_job(client, job_id, expected="failed")
        retry = await client.post(f"/api/jobs/{job_id}/retry")
        self.assertEqual(retry.status_code, 202, retry.text)
        job = await self._wait_for_job(client, job_id)
        self.assertEqual(job["attempt"], 1)

    async def _exercise_legacy_endpoint(self, client):
        response = await client.post("/api/index", json={"docs": [{
            "idx": "legacy-1", "title": "旧接口文档", "text": "旧接口仍然进入版本化知识库。"
        }]})
        self.assertEqual(response.status_code, 200, response.text)
        pointer = service._active_pointer()
        self.assertEqual(pointer["job_id"], response.json()["job_id"])
        documents = (await client.get("/api/documents")).json()["documents"]
        self.assertIn("legacy-1", {item["document_id"] for item in documents})


class ServiceStateHelperTests(unittest.TestCase):
    def test_incomplete_job_is_recovered_with_its_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.multiple(
                service,
                DATA_ROOT=root,
                JOB_DB_PATH=root / "jobs.sqlite3",
                ACTIVE_POINTER=root / "active.json",
            ):
                service.init_job_db()
                job_id = service.create_job({"documents": [{"document_id": "d1"}]})
                service.update_job(job_id, status="building", progress=60, message="building")
                self.assertEqual(service.recover_incomplete_jobs(), [job_id])
                record = service._get_job_record(job_id)
                self.assertEqual(record["status"], "queued")
                self.assertIn("d1", record["payload_json"])

    def test_seeded_openie_cache_is_filtered_to_current_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seed = root / "seed.json"
            seed.write_text(json.dumps({
                "schema_version": 2,
                "index_id": "old-kb",
                "source_model": "qwen-plus",
                "prompt_version": service.OPENIE_PROMPT_VERSION,
                "docs": [
                    {"passage": "保留内容", "extracted_entities": [], "extracted_triples": []},
                    {"passage": "已删除内容", "extracted_entities": [], "extracted_triples": []},
                ],
            }, ensure_ascii=False), encoding="utf-8")
            version = root / "version"
            with patch.multiple(service, INDEX_ID="new-kb"), patch.dict(
                "os.environ",
                {
                    "SYNAPSERAG_OPENIE_CACHE_SEED": str(seed),
                    "SYNAPSERAG_OPENIE_MODEL": "qwen-plus",
                },
                clear=False,
            ):
                config = service.make_config("openie", include_openie=True, save_dir=str(version))
                copied = service._copy_compatible_openie_cache(version, config, ["保留内容"])
            self.assertTrue(copied)
            target = json.loads((version / "openie" / "new-kb" / "openie_results.json").read_text(encoding="utf-8"))
            self.assertEqual(target["index_id"], "new-kb")
            self.assertEqual([item["passage"] for item in target["docs"]], ["保留内容"])


class RetrievalTracingTests(unittest.TestCase):
    def test_collector_and_store_preserve_task_context(self):
        with tempfile.TemporaryDirectory() as directory:
            collector = RetrievalTraceCollector(
                request_id="request-1",
                purpose="planning",
                query_ids=["query-1"],
                context={"workflow_id": "workflow-1", "task_id": "task-1"},
                index_id="index-1",
            )
            query = collector.begin_query(0, "test query")
            collector.record_channel(
                query,
                "dense",
                np.asarray([1, 0]),
                np.asarray([0.9, 0.7]),
                ["chunk-a", "chunk-b"],
            )
            collector.record_fusion(
                query,
                np.asarray([1]),
                np.asarray([0.8]),
                {1: {"graph": 0.3, "dense": 0.5}},
                ["chunk-a", "chunk-b"],
            )
            collector.record_evidence(0, [{"chunk_id": "source-chunk", "node_key": "chunk-b"}])
            collector.finish_query(query)
            collector.complete()

            path = Path(directory) / "traces.sqlite3"
            store = RetrievalTraceStore(path)
            store.initialize()
            store.save(collector.to_dict())
            reopened = RetrievalTraceStore(path)
            trace = reopened.get(collector.trace_id)
            self.assertEqual(trace["task_id"], "task-1")
            self.assertEqual(trace["queries"][0]["channel_results"]["dense"][0]["node_key"], "chunk-b")
            self.assertEqual(
                trace["queries"][0]["evidence"][0]["retrieval_reason"]["matched_channels"],
                ["dense", "graph"],
            )
            self.assertEqual(len(reopened.list(task_id="task-1")), 1)
            self.assertEqual(reopened.list(task_id="missing"), [])

    def test_weighted_fusion_reports_per_channel_contributions(self):
        document_ids, scores, contributions = SynapseRAG._weighted_score_fusion_with_contributions([
            ("graph", np.asarray([0, 1]), np.asarray([0.8, 0.2]), 0.4),
            ("dense", np.asarray([1, 0]), np.asarray([0.9, 0.3]), 0.5),
            ("lexical", np.asarray([1]), np.asarray([2.0]), 0.1),
        ])
        self.assertEqual(document_ids.tolist(), [1, 0])
        self.assertAlmostEqual(scores[0], 0.6)
        self.assertAlmostEqual(contributions[1]["dense"], 0.5)
        self.assertAlmostEqual(contributions[1]["lexical"], 0.1)

    def test_graph_overlay_uses_stable_node_keys_and_evidence_roles(self):
        entity_key = "entity-" + hashlib.md5("alpha".encode()).hexdigest()
        chunk_key = "chunk-evidence"

        class FakeEdge(dict):
            source = 0
            target = 1

            def attributes(self):
                return list(self.keys())

        class FakeGraph:
            vs = [{"name": entity_key}, {"name": chunk_key}]
            es = [FakeEdge(weight=0.8)]

            @staticmethod
            def incident(_vertex_id):
                return [0]

        class FakeStore:
            @staticmethod
            def get_row(key):
                return {"content": key}

        rag = type("FakeGraphRAG", (), {
            "graph": FakeGraph(),
            "node_name_to_vertex_idx": {entity_key: 0, chunk_key: 1},
            "entity_embedding_store": FakeStore(),
            "chunk_embedding_store": FakeStore(),
        })()
        queries = [{
            "selected_facts": [{"triple": ["alpha", "relates to", "beta"]}],
            "seed_nodes": [{"node_key": entity_key, "reset_score": 0.5}],
            "ppr_nodes": [],
            "channel_results": {"graph": [{"node_key": chunk_key}]},
            "fusion_results": [],
            "evidence": [{"chunk_id": chunk_key}],
        }]
        build_graph_overlays(rag, queries, max_nodes=10, max_edges=10)
        overlay = queries[0]["graph_overlay"]
        evidence_node = next(
            node for node in overlay["nodes"] if node["node_key"] == chunk_key
        )
        self.assertEqual(overlay["semantics"], "evidence_skeleton")
        self.assertIn("final_evidence", evidence_node["roles"])
        self.assertEqual(overlay["edges"][0]["source_key"], entity_key)
        self.assertEqual(queries[0]["candidate_overlay"]["edges"], [])
        self.assertEqual(queries[0]["evidence_paths"][0]["hop_count"], 1)
        focused = select_graph_overlay(
            queries[0], evidence_node_key=chunk_key, max_paths=1
        )
        self.assertEqual(focused["semantics"], "single_evidence_path")
        self.assertEqual(len(focused["edges"]), 1)

    def test_pdf_source_location_and_highlight_preview(self):
        try:
            import pymupdf
        except ImportError:
            self.skipTest("pymupdf is not installed")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rules.pdf"
            pdf = pymupdf.open()
            page = pdf.new_page()
            page.insert_text((72, 100), "Evidence text for precise source highlighting.")
            pdf.save(path)
            pdf.close()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            source_map = {
                "Evidence text for precise source highlighting.": [{
                    "chunk_id": "rules-chunk-00001",
                    "document_id": "rules",
                    "filename": "rules.pdf",
                    "page_start": 1,
                    "page_end": 1,
                    "heading_path": [],
                }]
            }
            manifest = {"documents": [{
                "document_id": "rules",
                "filename": "rules.pdf",
                "upload_path": str(path),
                "content_sha256": digest,
            }]}
            location = locate_chunk(source_map, manifest, "rules-chunk-00001")
            self.assertEqual(location["precision"], "rectangle")
            self.assertTrue(location["locations"][0]["rects"])
            payload, media_type, _ = highlighted_preview(
                source_map, manifest, "rules-chunk-00001"
            )
            self.assertEqual(media_type, "application/pdf")
            highlighted = pymupdf.open(stream=payload, filetype="pdf")
            self.assertGreaterEqual(len(list(highlighted[0].annots() or [])), 1)
            highlighted.close()

    def test_evidence_skeleton_preserves_low_score_connector(self):
        seed = "entity-seed"
        connector = "entity-connector"
        evidence = "chunk-final"

        class FakeEdge(dict):
            def __init__(self, source, target, weight):
                super().__init__(weight=weight)
                self.source = source
                self.target = target

            def attributes(self):
                return list(self.keys())

        class FakeGraph:
            vs = [{"name": seed}, {"name": connector}, {"name": evidence}]
            es = [FakeEdge(0, 1, 0.9), FakeEdge(1, 2, 0.7)]

        class FakeStore:
            @staticmethod
            def get_row(key):
                return {"content": key}

        rag = type("FakeGraphRAG", (), {
            "graph": FakeGraph(),
            "node_name_to_vertex_idx": {seed: 0, connector: 1, evidence: 2},
            "entity_embedding_store": FakeStore(),
            "chunk_embedding_store": FakeStore(),
        })()
        queries = [{
            "selected_facts": [],
            "seed_nodes": [{"node_key": seed, "reset_score": 1.0}],
            "ppr_nodes": [{"node_key": connector, "ppr_score": 0.01}],
            "channel_results": {"graph": [{"node_key": evidence}]},
            "fusion_results": [{"node_key": evidence, "final_score": 0.8}],
            "evidence": [{"node_key": evidence, "chunk_id": "logical-chunk"}],
        }]
        build_graph_overlays(rag, queries, max_nodes=5, max_edges=4, max_paths=1)
        path = queries[0]["evidence_paths"][0]
        self.assertEqual(path["nodes"], [seed, connector, evidence])
        self.assertEqual(path["connector_nodes"], [connector])
        connector_node = next(
            node for node in queries[0]["graph_overlay"]["nodes"]
            if node["node_key"] == connector
        )
        self.assertIn("retrieval_connector", connector_node["roles"])

        # Final passages can be PPR seeds too; focusing them must retain the path.
        queries[0]["seed_nodes"].append({"node_key": evidence, "reset_score": 0.5})
        build_graph_overlays(rag, queries, max_nodes=5, max_edges=4, max_paths=1)
        self.assertEqual(queries[0]["evidence_paths"][0]["nodes"], [seed, connector, evidence])
        focused = select_graph_overlay(queries[0], evidence_node_key=evidence, max_paths=1)
        self.assertEqual(len(focused["edges"]), 2)

    def test_legacy_overlay_is_reduced_without_rebuilding_trace(self):
        query = {"graph_overlay": {
            "semantics": "high_contribution_neighborhood",
            "nodes": [
                {"node_key": "seed", "roles": ["ppr_seed"]},
                {"node_key": "noise", "roles": ["retrieval_neighborhood"]},
                {"node_key": "evidence", "roles": ["final_evidence"]},
            ],
            "edges": [
                {"source_key": "seed", "target_key": "noise"},
                {"source_key": "noise", "target_key": "evidence"},
            ],
        }}
        skeleton = select_graph_overlay(query)
        candidates = select_graph_overlay(query, view="candidates")
        self.assertEqual(skeleton["semantics"], "legacy_evidence_terminals")
        self.assertEqual({node["node_key"] for node in skeleton["nodes"]}, {"seed", "evidence"})
        self.assertEqual(skeleton["edges"], [])
        self.assertEqual(candidates["edges"], [])


if __name__ == "__main__":
    unittest.main()
