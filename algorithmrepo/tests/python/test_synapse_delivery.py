"""Run with services/, services/synapserag and its src/ on PYTHONPATH."""

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
import igraph
import requests

import api_server as service
import synapse_algorithms


class DeliveryTests(unittest.TestCase):
    def test_service_graph_and_index_contracts(self):
        asyncio.run(self.exercise_graph())

    async def exercise_graph(self):
        graph = igraph.Graph(edges=[(0, 1), (1, 2), (1, 3)])
        graph.vs["name"] = ["entity-a", "entity-b", "chunk-c", "entity-d"]
        graph.es["weight"] = [0.8, 0.7, 0.6]
        store = SimpleNamespace(get_row=lambda key: {"content": key})
        rag = SimpleNamespace(graph=graph, entity_embedding_store=store, chunk_embedding_store=store)
        trace = {"trace_id": "old", "index_id": "old-index", "index_version": "v1",
                 "queries": [{"query_trace_id": "q1", "graph_overlay": {
                     "nodes": [{"node_key": "entity-a"}], "edges": []}}]}
        state = service.app.state
        saved = dict(state._state)
        state.rag = rag
        state.source_map = {}
        state.active_manifest = {"index_id": "new-index", "job_id": "v2"}
        state.query_lock = asyncio.Lock()
        state.trace_store = SimpleNamespace(get=lambda key: trace if key == "old" else None)
        try:
            with patch.dict("os.environ", {"SYNAPSERAG_API_TOKEN": "test-secret"}):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=service.app), base_url="http://test") as client:
                    response = await client.post("/api/graph/explore", json={"operation": "neighbors", "entity_id": "entity-a"})
                    self.assertEqual(response.status_code, 401)
                    client.headers["Authorization"] = "Bearer test-secret"
                    response = await client.post("/api/graph/explore", json={"operation": "neighbors", "entity_id": "entity-a", "max_hops": 2})
                    self.assertEqual(len(response.json()["nodes"]), 4)
                    response = await client.post("/api/graph/explore", json={"operation": "neighbors", "entity_id": "entity-a", "max_nodes": 1})
                    self.assertTrue(response.json()["truncated"])
                    self.assertEqual(len(response.json()["nodes"]), 1)
                    response = await client.post("/api/graph/explore", json={"operation": "paths", "source_id": "entity-a", "target_id": "chunk-c"})
                    self.assertEqual(response.json()["paths"][0]["nodes"], ["entity-a", "entity-b", "chunk-c"])
                    for body, status in [
                        ({"operation": "paths"}, 422),
                        ({"operation": "neighbors", "entity_id": "missing"}, 404),
                        ({"operation": "neighbors", "entity_id": "entity-a", "index_id": "wrong"}, 409),
                        ({"operation": "trace", "trace_id": "missing"}, 404),
                    ]:
                        response = await client.post("/api/graph/explore", json=body)
                        self.assertEqual(response.status_code, status, response.text)
                    state.rag = None
                    response = await client.post("/api/graph/explore", json={"operation": "trace", "trace_id": "old"})
                    self.assertEqual(response.json()["index_version"], "v1")
                    self.assertEqual(response.json()["index_id"], "old-index")
                    response = await client.post("/api/graph/explore", json={"operation": "neighbors", "entity_id": "entity-a"})
                    self.assertEqual(response.status_code, 503)
                    state.rag = rag
                    response = await client.post("/api/retrieve", json={"request_id": "r", "index_id": "wrong", "queries": [{"query_id": "q", "text": "hello"}]})
                    self.assertEqual(response.status_code, 409)
                    response = await client.post("/api/retrieve", json={"request_id": "r", "queries": [{"query_id": "q", "text": "a"}, {"query_id": "q", "text": "b"}]})
                    self.assertEqual(response.status_code, 422)
        finally:
            state._state.clear()
            state._state.update(saved)

    def test_adapter_preserves_payload_errors_and_trace(self):
        asyncio.run(self.exercise_adapter())

    async def exercise_adapter(self):
        body = {"request_id": "r", "algorithm_id": "synapse_rag_retriever",
                "version": "2.0.0", "inputs": {"queries": [{"query_id": "q", "text": "hello"}]}}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=synapse_algorithms.retriever), base_url="http://test") as client:
            data = {"status": "success", "results": [{"query_id": "q", "evidence": []}], "trace_id": "real-trace", "retrieval_profile": {}}
            with patch.object(requests, "post", return_value=SimpleNamespace(status_code=200, json=lambda: data)) as post:
                result = (await client.post("/predict", json=body)).json()
                self.assertTrue(result["ok"])
                self.assertEqual(result["outputs"]["trace_id"], "real-trace")
                self.assertEqual(post.call_args.kwargs["json"]["request_id"], "r")
                self.assertTrue(post.call_args.kwargs["json"]["explain"]["enabled"])
            with patch.object(requests, "post", side_effect=requests.Timeout):
                result = (await client.post("/predict", json=body)).json()
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"], "RAG_TIMEOUT")
            with patch.object(requests, "post", return_value=SimpleNamespace(status_code=503, json=lambda: {"detail": {"error_code": "INDEX_NOT_READY", "message": "empty"}})):
                result = (await client.post("/predict", json=body)).json()
                self.assertEqual(result["error"]["code"], "INDEX_NOT_READY")


if __name__ == "__main__":
    unittest.main()
