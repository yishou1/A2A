# Qwen3-1.7B 本地部署与测试流程

## 1. 部署说明

当前使用 Ollama 在 WSL 中部署 `qwen3:1.7b`，模型为 Q4 量化版本，可在 RTX 3060 Laptop 6GB 显存上运行。

部署位置：

```text
Ollama: /home/dell/project_bupt/tools/ollama-runtime
模型:   /home/dell/project_bupt/local_models/ollama
```

调用流程：

```text
Qwen3-1.7B
  -> decision_planning_agent / compliance_authorization_agent
  -> algolib_server
  -> decision_planning_core / compliance_authorization_core
  -> ONNX Runtime
```

LLM 负责理解任务和选择算法，具体方案评分与合规判断仍由算法库完成。

## 2. 启动 Ollama

打开终端 1：

```bash
cd /home/dell/project_bupt/A2A
bash scripts/start_local_qwen_ollama.sh
```

首次部署或模型不存在时，在另一个终端执行：

```bash
/home/dell/project_bupt/tools/ollama-runtime/bin/ollama pull qwen3:1.7b
```

检查模型：

```bash
/home/dell/project_bupt/tools/ollama-runtime/bin/ollama list
/home/dell/project_bupt/tools/ollama-runtime/bin/ollama ps
```

正常情况下，`ollama ps` 应显示 `100% GPU` 和 `CONTEXT 4096`。

## 3. 单独测试本地模型

```bash
curl http://127.0.0.1:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3:1.7b",
    "messages": [
      {"role": "system", "content": "只返回合法 JSON，不要输出 Markdown。"},
      {"role": "user", "content": "返回一个 JSON 对象，字段 ok 的值为 true。"}
    ],
    "reasoning_effort": "none",
    "response_format": {"type": "json_object"},
    "temperature": 0.1,
    "max_tokens": 128
  }'
```

预期结果：

```json
{"ok": true}
```

返回内容不应包含 `<think>`。

## 4. 启动算法服务

打开终端 2，启动方案生成算法：

```bash
cd /home/dell/project_bupt/A2A-zsl-algorithmrepo
export A2A_REPO_ROOT=/home/dell/project_bupt/A2A
export PORT=9020
/home/dell/project_bupt/A2A/.venv/bin/python services/decision_planning_core/app/main.py
```

打开终端 3，启动规则授权算法：

```bash
cd /home/dell/project_bupt/A2A-zsl-algorithmrepo
export A2A_REPO_ROOT=/home/dell/project_bupt/A2A
export PORT=9021
/home/dell/project_bupt/A2A/.venv/bin/python services/compliance_authorization_core/app/main.py
```

打开终端 4，启动算法库：

```bash
cd /home/dell/project_bupt/A2A-zsl-algorithmrepo
./build/algolib_server --host 127.0.0.1 --port 8088
```

检查算法目录：

```bash
curl http://127.0.0.1:8088/algorithms
```

应能看到：

- `decision_planning_core`
- `compliance_authorization_core`

## 5. 配置本地模型环境

启动 Agent 前，在对应终端设置：

```bash
export ENABLE_LLM=true
export LLM_PROVIDER=openai_compatible
export TOOL_LLM_URL=http://127.0.0.1:11434/v1
export TOOL_LLM_NAME=qwen3:1.7b
export API_KEY=ollama
export LLM_TIMEOUT_SECONDS=120
export LLM_MAX_TOKENS=1024
export LLM_TEMPERATURE=0.1
export LLM_JSON_MODE=true
export LLM_STRIP_THINKING=true
export LLM_JSON_RETRY_COUNT=1
export LLM_REASONING_EFFORT=none

export DECISION_AGENT_BACKEND=algolib
export ALGOLIB_BASE_URL=http://127.0.0.1:8088
export ALGOLIB_TIMEOUT_SECONDS=20
```

## 6. 启动两个 Agent

打开终端 5，设置上面的环境变量后启动方案生成 Agent：

```bash
cd /home/dell/project_bupt/A2A
.venv/bin/python -m decision_agents serve \
  --agent decision_planning \
  --host 127.0.0.1 \
  --port 10202
```

打开终端 6，设置相同环境变量后启动规则授权 Agent：

```bash
cd /home/dell/project_bupt/A2A
.venv/bin/python -m decision_agents serve \
  --agent compliance_authorization \
  --host 127.0.0.1 \
  --port 10203
```

## 7. 测试方案生成 Agent

```bash
curl -X POST http://127.0.0.1:10202/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "dp-local-001",
    "method": "message/send",
    "params": {
      "message": {
        "messageId": "dp-local-msg-001",
        "role": "user",
        "parts": [{
          "kind": "text",
          "text": "{\"request_id\":\"dp-local-001\",\"agent_profile\":{\"compute_budget\":\"medium\",\"risk_policy\":\"balanced\"},\"risk_assessments\":[{\"target_id\":\"TGT-1\",\"priority\":1,\"risk\":\"high\",\"threat_score\":82.0,\"probability\":0.74,\"rationale\":\"High-priority monitoring target.\"}],\"scheduled_tasks\":[{\"id\":\"TASK-1\",\"target_id\":\"TGT-1\",\"priority\":1,\"task_type\":\"monitor\",\"required_resource_types\":[\"uav\"]}],\"resources\":[{\"id\":\"UAV-1\",\"type\":\"uav\",\"status\":\"available\",\"capacity\":1.0}],\"planning_objectives\":[\"risk_first\"],\"constraints\":[\"simulation-only decision-support\"],\"authorization\":{\"status\":\"pending_review\"}}"
        }]
      }
    }
  }'
```

重点检查：

- `status.state` 为 `completed`
- `selected_algorithms` 包含 `decision_planning_core`
- `candidate_plans` 包含具体方案内容
- `recommended_plan_id` 有值
- `decision_planning_lr.backend` 为 `onnxruntime`

## 8. 测试规则授权 Agent

```bash
curl -X POST http://127.0.0.1:10203/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "ca-local-001",
    "method": "message/send",
    "params": {
      "message": {
        "messageId": "ca-local-msg-001",
        "role": "user",
        "parts": [{
          "kind": "text",
          "text": "{\"request_id\":\"ca-local-001\",\"candidate_plans\":[{\"id\":\"PLAN-PRIORITY-MONITOR\",\"name\":\"重点目标监测方案\",\"status\":\"candidate\",\"target_ids\":[\"TGT-1\"],\"assigned_resources\":[\"UAV-1\"],\"actions\":[\"使用无人机执行重点区域监测\"],\"expected_effects\":[\"提高目标态势感知能力\"],\"score\":86.0,\"rationale\":\"优先监测高风险目标。\"}],\"authorization\":{\"status\":\"pending_review\",\"scope\":[\"uav_monitoring\"],\"approved_plan_ids\":[\"PLAN-PRIORITY-MONITOR\"]},\"constraints\":[{\"max_risk\":\"medium\",\"human_review_required\":true}]}"
        }]
      }
    }
  }'
```

重点检查：

- `status.state` 为 `completed`
- `selected_algorithms` 包含 `compliance_authorization_core`
- `decision` 为 `approved`、`blocked` 或 `review_required`
- 授权待审核时返回 `review_required` 属于正常结果
- `compliance_authorization_lr.backend` 为 `onnxruntime`

## 9. 运行自动化测试

```bash
cd /home/dell/project_bupt/A2A
.venv/bin/python -m pytest \
  tests/test_llm_client.py \
  tests/test_decision_agents_algolib_runtime.py \
  tests/test_decision_agents_a2a.py \
  -q
```

当前验证结果：

```text
12 passed
```

## 10. 验收标准

满足以下条件即可认为本地部署成功：

1. Ollama 能识别 `qwen3:1.7b`，并显示 `100% GPU`。
2. 本地 Chat Completions 接口返回合法 JSON，不包含 `<think>`。
3. 两个 Agent 都能选择正确的 core 算法包。
4. 方案 Agent 能返回具体候选方案和推荐方案。
5. 规则 Agent 能返回合规决定、违规项和人工复核建议。
6. 算法结果中的 LR 模型使用 `onnxruntime`。
