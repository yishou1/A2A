# GPT-4o-mini 与 zsl AlgorithmRepo 联调

## 1. 运行结构

```text
Commander / upstream Agent
        -> Nacos discovers role=track_threat
        -> POST /sendMessage with required_skill
Track Threat Agent
        -> GET  zsl AlgorithmRepo /algorithms
        -> Azure GPT-4o-mini returns JSON algorithm_calls
        -> Agent validates allowlist/version/backend
        -> POST zsl AlgorithmRepo /run
        -> merge validated outputs into TrackState/artifact
        -> local algorithm fallback on optional remote failure
```

GPT-4o-mini only selects algorithms. It does not generate trajectory points,
risk scores, URLs, or algorithm outputs. `text-embedding-3-small` is an
embedding deployment and cannot replace the chat deployment in this flow.

## 2. Track Threat allowlist

Only these zsl packages can be selected:

```text
multimodal_feature_fuser
trajectory_predictor
graph_relation_reasoner
threat_priority_random_forest
```

An LLM response outside this list, outside the requested Skill, or with a wrong
version/backend is rejected. When `TOOL_LLM_REQUIRED=false`, the Agent uses the
deterministic Skill mapping. When `ALGORITHM_LIBRARY_REQUIRED=false`, runtime
errors fall back to the Agent-local implementations.

## 3. Start zsl AlgorithmRepo

Use a separate clone or Git worktree so the Agent branch and zsl branch can run
at the same time:

```bash
git fetch origin zsl/algorithmrepo
git worktree add ../A2A-algorithmrepo origin/zsl/algorithmrepo
cd ../A2A-algorithmrepo
```

Build the central library:

```bash
cmake -S . -B build
cmake --build build -j
```

Start the Track Threat Python service in terminal 1:

```bash
uv run --with-requirements services/requirements.txt \
  python services/track_threat_algorithms/app/main.py
```

It listens on `127.0.0.1:9022`.

Register and activate the four packages in terminal 2:

```bash
for algorithm_id in \
  multimodal_feature_fuser \
  trajectory_predictor \
  graph_relation_reasoner \
  threat_priority_random_forest
do
  ./build/algolib register "examples/${algorithm_id}/1.0.0"
  ./build/algolib activate "${algorithm_id}" 1.0.0 python_http_service
done
```

Start the central server:

```bash
./build/algolib_server --host 127.0.0.1 --port 8088
```

Verify the catalog:

```bash
curl http://127.0.0.1:8088/algorithms
```

## 4. Configure the Track Threat Agent

Do not write the real API key into Git. Inject it through the shell, secret
manager, or deployment platform:

```bash
export ALGORITHM_LIBRARY_ENABLED=true
export ALGORITHM_LIBRARY_REQUIRED=false
export ALGOLIB_BASE_URL=http://127.0.0.1:8088
export ALGOLIB_TIMEOUT_SECONDS=10

export ENABLE_LLM=true
export TOOL_LLM_REQUIRED=false
export LLM_PROVIDER=azure_openai
export AZURE_OPENAI_ENDPOINT=https://YOUR-RESOURCE.openai.azure.com
export AZURE_OPENAI_CHAT_DEPLOYMENT=gpt-4o-mini
export AZURE_OPENAI_API_VERSION=2024-12-01-preview
export AZURE_OPENAI_API_KEY='injected-secret'
export LLM_TIMEOUT_SECONDS=30
```

For an offline integration test, use:

```bash
export ENABLE_LLM=false
```

The Agent still calls AlgorithmRepo, but uses the deterministic Skill mapping
instead of the cloud planner.

Start the Agent:

```bash
cd track_threat_agent
./scripts/start_track_threat_agent.sh
```

## 5. What to inspect

The following endpoints expose the integration without leaking the API key:

```text
GET /health
GET /ready
GET /algorithms
GET /.well-known/agent-card.json
```

Each result artifact contains:

```text
trace.algorithm_library.enabled
trace.algorithm_library.planner_mode
trace.algorithm_library.planned_algorithms
trace.algorithm_library.executions
trace.algorithm_library.local_fallbacks
```

`planner_mode` values:

```text
azure_gpt_4o_mini       Azure chat planner produced a valid plan
deterministic           LLM intentionally disabled
deterministic_fallback  LLM optional and unavailable/invalid
local_only              AlgorithmRepo disabled
local_fallback          AlgorithmRepo optional and unavailable
```

Remote prediction points are marked with:

```json
{
  "model_used": "algorithm_library",
  "algorithm_id": "trajectory_predictor",
  "prediction_provenance": {
    "algorithm": "trajectory_predictor",
    "role": "primary",
    "is_trained_model": true,
    "execution_location": "zsl_algorithm_library"
  }
}
```

## 6. Safety boundary

This integration remains limited to simulation situation awareness, trajectory
prediction, grouping, protected-asset impact analysis, and risk-priority
ranking. It does not generate weapon control, attack recommendations, guidance,
fire-control commands, or engagement decisions.
