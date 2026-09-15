"""Launch the standalone SynapseRAG algorithms and AlgoLib gateway."""

import os
import json
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import requests


ROOT = Path(__file__).resolve().parents[1]


def main():
    env = os.environ.copy()
    env.setdefault("SYNAPSERAG_SAVE_DIR", str(ROOT / "runtime-data" / "synapserag"))
    env.setdefault("ALGOLIB_REGISTRY_PATH", str(ROOT / "runtime-data" / "synapse-registry.json"))
    env["SYNAPSERAG_BASE_URL"] = "http://127.0.0.1:8000"
    data_dir = Path(env["SYNAPSERAG_SAVE_DIR"]).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    Path(env["ALGOLIB_REGISTRY_PATH"]).parent.mkdir(parents=True, exist_ok=True)
    binary = Path(env.get("ALGOLIB_BIN", str(ROOT / "build" / "algolib")))
    server = Path(env.get("ALGOLIB_SERVER_BIN", str(ROOT / "build" / "algolib_server")))
    if not binary.is_file() or not server.is_file():
        raise SystemExit("Build AlgoLib first: cmake -S . -B build && cmake --build build -j2")
    for port in (8000, 9048, 9049, 8088):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    service = ROOT / "services" / "synapserag"
    rag_env = {**env, "PYTHONPATH": str(service) + os.pathsep + str(service / "src")}
    adapter_env = {**env, "PYTHONPATH": str(ROOT / "services")}
    children = []

    def stop(*_args):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        commands = [
            ([sys.executable, "-m", "uvicorn", "api_server:app", "--host", "127.0.0.1", "--port", "8000"], rag_env, service),
            ([sys.executable, "-m", "uvicorn", "synapse_algorithms:retriever", "--port", "9048"], adapter_env, ROOT),
            ([sys.executable, "-m", "uvicorn", "synapse_algorithms:graph_explorer", "--port", "9049"], adapter_env, ROOT),
        ]
        for command, child_env, cwd in commands:
            children.append(subprocess.Popen(command, env=child_env, cwd=cwd))
        children.append(subprocess.Popen([str(server), "--port", "8088"], env=env, cwd=ROOT))
        print("Gateway: http://127.0.0.1:8088/run; knowledge management: http://127.0.0.1:8000/docs", flush=True)
        print("Waiting for an active index, then validating and activating both algorithms.", flush=True)
        while True:
            if any(child.poll() is not None for child in children):
                raise RuntimeError("A service exited during startup")
            try:
                health = requests.get(env["SYNAPSERAG_BASE_URL"] + "/api/health", timeout=3)
                if health.json().get("indexed") is True:
                    break
            except (requests.RequestException, ValueError):
                pass
            time.sleep(1)
        register_algorithms(binary, env)
        while all(child.poll() is None for child in children):
            time.sleep(1)
        raise RuntimeError("A service exited; stopping the stack")
    except KeyboardInterrupt:
        pass
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


def register_algorithms(binary, env):
    # Registration runs a real golden request. Trace IDs are deployment-specific,
    # so keep generated acceptance packages outside the source distribution.
    headers = {"Authorization": "Bearer " + env.get("SYNAPSERAG_API_TOKEN", "")}
    response = requests.post(env["SYNAPSERAG_BASE_URL"] + "/api/retrieve",
                             headers=headers, timeout=65,
                             json={"request_id": "delivery-registration",
                                   "queries": [{"query_id": "probe", "text": env.get("SYNAPSERAG_ACCEPTANCE_QUERY", "Summarize the indexed documents")}],
                                   "explain": {"enabled": True}, "top_k": 1})
    response.raise_for_status()
    trace_id = response.json().get("trace_id")
    if not trace_id:
        raise RuntimeError("Trace persistence must be enabled for graph acceptance")
    for algorithm, version in [("synapse_rag_retriever", "2.0.0"), ("synapse_graph_explorer", "1.0.0")]:
        package = Path(env["ALGOLIB_REGISTRY_PATH"]).parent / "packages" / algorithm / version
        shutil.copytree(ROOT / "examples" / algorithm / version, package, dirs_exist_ok=True)
        golden = package / "golden_cases" / "case_001_request.json"
        body = json.loads(golden.read_text())
        if algorithm == "synapse_graph_explorer":
            body["inputs"] = {"operation": "trace", "trace_id": trace_id}
        golden.write_text(json.dumps(body, indent=2) + "\n")
        exists = subprocess.run([str(binary), "show-card", algorithm, version, "python_http_service"],
                                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        command = ([str(binary), "validate", algorithm, version, "python_http_service"] if exists.returncode == 0
                   else [str(binary), "register", str(package)])
        subprocess.run(command, env=env, check=True)
        subprocess.run([str(binary), "activate", algorithm, version, "python_http_service"], env=env, check=True)
    print("Both SynapseRAG algorithms are active.", flush=True)


if __name__ == "__main__":
    main()
