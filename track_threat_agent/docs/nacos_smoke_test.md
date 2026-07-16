# Nacos Smoke Test

本文档用于验证 `track_threat_agent` 是否能作为 A2A/Nacos 下游 Agent 被发现、被调用，并在处理完成后回到可调度状态。

安全边界：本 Agent 只输出仿真态势、航迹预测、保护资产影响和关注优先级排序，不做真实武器控制、打击建议、制导或交战决策。

## 1. 启动 Nacos

在仓库根目录启动 Nacos：

```bash
cd /Users/mac/Desktop/yishou1-A2A
docker compose up -d nacos
```

Apple Silicon 机器使用 `nacos/nacos-server:v2.2.3` 时需要 `platform: linux/amd64`。本仓库的 `docker-compose.yml` 已经包含该配置。

健康检查：

```bash
curl http://127.0.0.1:8848/nacos/v1/ns/operator/metrics
```

期望输出：

```json
{"status":"UP"}
```

## 2. 启动 Track Threat Agent

算法和 TorchScript 模型由 Agent 本进程加载，不需要额外启动算法库 HTTP 服务。

```bash
cd /Users/mac/Desktop/yishou1-A2A/track_threat_agent

NACOS_ENABLED=true \
NACOS_SERVER=127.0.0.1:8848 \
NACOS_NAMESPACE=public \
SERVICE_NAME=A2A-Agent \
SERVICE_IP=127.0.0.1 \
SERVICE_PORT=8102 \
AGENT_ID=track-threat-group-agent-01 \
AGENT_ROLE=track_threat \
AGENT_STATUS=idle \
HEARTBEAT_INTERVAL=5 \
PYTHONPATH=.. uv run --with-requirements ../requirements.txt --with-requirements requirements.txt \
  uvicorn app.main:app --host 127.0.0.1 --port 8102
```

如需替换模型，设置 `ST_GNN_AIRCRAFT_MODEL_DIR` 或 `ST_GNN_SHIP_MODEL_DIR`；不设置时使用仓库内置 bundle。

## 3. 验证 Agent 健康和模型状态

```bash
curl http://127.0.0.1:8102/health
curl http://127.0.0.1:8102/models
```

关键字段应为：

```json
{
  "status": "ok",
  "agent_status": "idle",
  "nacos": {
    "enabled": true,
    "registered": true,
    "service_name": "A2A-Agent",
    "role": "track_threat"
  }
}
```

## 4. 验证 Nacos 直接注册结果

```bash
curl 'http://127.0.0.1:8848/nacos/v1/ns/instance/list?serviceName=A2A-Agent'
```

应能看到一个 `127.0.0.1:8102` 实例，且 metadata 至少包含：

```text
role=track_threat
status=idle
send_message_endpoint=http://127.0.0.1:8102/sendMessage
health_endpoint=http://127.0.0.1:8102/health
heartbeat_ts=<recent unix timestamp>
```

`heartbeat_ts` 必须持续刷新。师兄仓库的 `NacosRegistry` 会用该字段判断实例是否新鲜。

## 5. 验证师兄仓库发现逻辑

```bash
cd /Users/mac/Desktop/yishou1-A2A

PYTHONPATH=. uv run --with-requirements requirements.txt python - <<'PY'
from registry.nacos_manager import NacosRegistry

r = NacosRegistry()
items = r.discover_service("A2A-Agent", {"role": "track_threat", "status": "idle"})
print({"discover_count": len(items)})
if items:
    item = items[0]
    print({
        "ip": item["ip"],
        "port": item["port"],
        "role": item["metadata"].get("role"),
        "status": item["metadata"].get("status"),
        "send_message_endpoint": item["metadata"].get("send_message_endpoint"),
    })
r.close()
PY
```

期望输出：

```json
{
  "discover_count": 1,
  "ip": "127.0.0.1",
  "port": 8102,
  "role": "track_threat",
  "status": "idle",
  "send_message_endpoint": "http://127.0.0.1:8102/sendMessage"
}
```

## 6. 通过发现到的 endpoint 调用 A2A

```bash
cd /Users/mac/Desktop/yishou1-A2A

PYTHONPATH=. uv run --with-requirements requirements.txt python - <<'PY'
import json
import urllib.request
from pathlib import Path
from registry.nacos_manager import NacosRegistry

registry = NacosRegistry()
instances = registry.discover_service("A2A-Agent", {"role": "track_threat", "status": "idle"})
registry.close()
if not instances:
    raise SystemExit("No track_threat Agent discovered")

endpoint = instances[0]["metadata"]["send_message_endpoint"]
payload = json.loads(Path("track_threat_agent/sample_data/group_scene.json").read_text())
body = {
    "workflow_id": "wf-nacos-smoke-001",
    "work_item": "track-threat-nacos-smoke-001",
    "command": "analyze_perception_result",
    "role": "track_threat",
    "work_list": [
        {"activity": "perception_fusion", "role": "recon"},
        {"activity": "track_threat_analysis", "role": "track_threat"},
        {"activity": "situation_display", "role": "commander"}
    ],
    "payload": payload
}

req = urllib.request.Request(
    endpoint,
    data=json.dumps(body).encode("utf-8"),
    headers={"Content-Type": "application/json", "Authorization": "Bearer demo-token"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=15) as response:
    data = json.loads(response.read().decode("utf-8"))

summary = data["artifact"]["summary"]
print(json.dumps({
    "status": data["status"],
    "track_count": summary["track_count"],
    "threat_count": summary["threat_count"],
    "group_count": summary["group_count"],
    "protected_asset_count": summary["protected_asset_count"],
    "asset_impact_count": summary["asset_impact_count"],
}, ensure_ascii=False, indent=2))
PY
```

期望输出示例：

```json
{
  "status": "completed",
  "track_count": 7,
  "threat_count": 7,
  "group_count": 2,
  "protected_asset_count": 4,
  "asset_impact_count": 28
}
```

## 7. 停止服务

停止 Agent：在 Agent 终端按 `Ctrl+C`。

停止 Nacos：

```bash
cd /Users/mac/Desktop/yishou1-A2A
docker compose stop nacos
```
