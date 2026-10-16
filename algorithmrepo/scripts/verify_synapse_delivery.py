"""Exercise the real standalone stack using an isolated existing index copy."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import requests


ROOT = Path(__file__).resolve().parents[1]


def algorithms_active(registry):
    active = {
        (entry.get("key", {}).get("algorithm_id"),
         entry.get("key", {}).get("version"),
         entry.get("key", {}).get("backend_type"))
        for entry in registry.get("entries", [])
        if entry.get("status") == "active"
    }
    return {("synapse_rag_retriever", "2.0.0", "python_http_service"),
            ("synapse_graph_explorer", "1.0.0", "python_http_service")} <= active


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-data", type=Path, required=True)
    parser.add_argument("--index-id", required=True)
    parser.add_argument("--query", default="What authorization is required?")
    parser.add_argument("--report", type=Path, default=ROOT / "runtime-data" / "delivery-report.json")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="synapse-delivery-") as temporary:
        runtime = Path(temporary)
        data = runtime / "data"
        shutil.copytree(args.seed_data, data, ignore=shutil.ignore_patterns("jobs.sqlite3*", "retrieval_traces.sqlite3*"))
        # Test legacy indexes directly; managed indexes may contain absolute
        # active pointers and should be exported before using this harness.
        env = {**os.environ, "SYNAPSERAG_SAVE_DIR": str(data),
               "SYNAPSERAG_INDEX_ID": args.index_id,
               "ALGOLIB_REGISTRY_PATH": str(runtime / "registry.json"),
               "SYNAPSERAG_ACCEPTANCE_QUERY": args.query}
        log = open(runtime / "stack.log", "w")
        child = subprocess.Popen([sys.executable, str(ROOT / "scripts/start_synapse_stack.py")], env=env, stdout=log, stderr=log)
        try:
            request = {"request_id": "delivery-e2e", "algorithm_id": "synapse_rag_retriever",
                       "version": "2.0.0", "backend_type": "python_http_service",
                       "inputs": {"index_id": args.index_id, "queries": [{"query_id": "q1", "text": args.query}],
                                  "explain": {"enabled": True}, "top_k": 3}, "params": {}}
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    raise RuntimeError("stack exited")
                try:
                    registry = json.loads((runtime / "registry.json").read_text())
                    if algorithms_active(registry):
                        break
                except (OSError, ValueError):
                    pass
                time.sleep(1)
            else:
                raise RuntimeError("activation timed out")
            result = requests.post("http://127.0.0.1:8088/run", json=request, timeout=90).json()
            if not result.get("ok"):
                raise RuntimeError(json.dumps(result))
            outputs = result["outputs"]
            if not outputs["results"][0]["evidence"] or not outputs.get("trace_id"):
                raise RuntimeError("missing real evidence or trace")
            graph_request = {**request, "algorithm_id": "synapse_graph_explorer", "version": "1.0.0",
                             "inputs": {"operation": "trace", "trace_id": outputs["trace_id"]}}
            graph_result = requests.post("http://127.0.0.1:8088/run", json=graph_request, timeout=90).json()
            if not graph_result.get("ok") or not graph_result["outputs"]["nodes"]:
                raise RuntimeError(json.dumps(graph_result))
            entity = next((n["node_key"] for n in graph_result["outputs"]["nodes"] if n.get("node_type") == "entity"), None)
            neighbors = None
            paths = None
            if entity:
                graph_request["inputs"] = {"operation": "neighbors", "index_id": args.index_id, "entity_id": entity}
                neighbors = requests.post("http://127.0.0.1:8088/run", json=graph_request, timeout=90).json()
                if not neighbors.get("ok"):
                    raise RuntimeError(json.dumps(neighbors))
                target = next((n["node_key"] for n in neighbors["outputs"]["nodes"] if n["node_key"] != entity), None)
                if target:
                    graph_request["inputs"] = {"operation": "paths", "index_id": args.index_id,
                                               "source_id": entity, "target_id": target}
                    paths = requests.post("http://127.0.0.1:8088/run", json=graph_request, timeout=90).json()
                    if not paths.get("ok") or not paths["outputs"]["paths"]:
                        raise RuntimeError(json.dumps(paths))
            if not outputs["retrieval_profile"].get("index_version"):
                raise RuntimeError("missing index version")
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps({"retrieval": result, "trace": graph_result, "neighbors": neighbors, "paths": paths}, ensure_ascii=False, indent=2))
            print("Real /run acceptance passed. Report:", args.report)
        except Exception:
            log.flush()
            print((runtime / "stack.log").read_text()[-12000:], file=sys.stderr)
            raise
        finally:
            child.terminate()
            try:
                child.wait(timeout=40)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
            log.close()


if __name__ == "__main__":
    main()
