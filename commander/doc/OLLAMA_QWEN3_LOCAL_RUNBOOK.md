# Ollama + Qwen3-1.7B 本地运行手册

## 已部署位置

```text
/home/dell/project_bupt/tools/ollama-runtime
/home/dell/project_bupt/local_models/ollama
```

Ollama 采用项目内安装，不依赖 `/usr/bin`，也不需要 systemd 服务。

## 启动模型服务

```bash
cd /home/dell/project_bupt/A2A
bash scripts/start_local_qwen_ollama.sh
```

默认配置：

- 监听 `127.0.0.1:11434`
- 上下文长度 4096
- 单并发
- 模型目录 `/home/dell/project_bupt/local_models/ollama`

## 下载或检查模型

启动 Ollama 服务后，在另一个终端执行：

```bash
/home/dell/project_bupt/tools/ollama-runtime/bin/ollama pull qwen3:1.7b
/home/dell/project_bupt/tools/ollama-runtime/bin/ollama list
```

## 单独测试模型

```bash
curl http://127.0.0.1:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3:1.7b",
    "messages": [
      {"role": "system", "content": "只返回合法 JSON。"},
      {"role": "user", "content": "返回 {\"ok\": true}"}
    ],
    "reasoning_effort": "none",
    "response_format": {"type": "json_object"},
    "temperature": 0.1,
    "max_tokens": 128
  }'
```

## A2A 环境变量

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
```

## GPU 检查

模型首次请求后执行：

```bash
nvidia-smi
```

如果 Ollama 未使用 GPU，查看服务启动日志中的 `inference compute`。正常情况下应显示 `library=CUDA` 和 RTX 3060 Laptop GPU。
