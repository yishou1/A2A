# anti_jam_mcdm_router — 抗干扰 MCDM/SAW 情报分发路由

> 算法 ID：`anti_jam_mcdm_router`（端口 9030）  
> **工业默认：SAW 多属性决策选信道**；可选 PPO 信道策略作学习增强。

## 生产默认（推荐）

**方法**：Simple Additive Weighting（SAW）多属性决策选信道 + 角色优先级分发。

| 出处 | 用途 |
|------|------|
| Hwang & Yoon (1981) *Multiple Attribute Decision Making* | 主决策规则 |
| FHSS / 备份链路 ECCM | 强干扰下信道候选 |
| DiffServ / 战术消息优先级 | 订阅角色 QoS |
| Schulman et al. (2017) PPO | **可选**学习增强 |

输出含 `algorithm_provenance` 与 `channel_decision_trace`，便于审计。

```python
from algorithms.anti_jam_mcdm_router import predict

out = predict(
    {
        "packet": {"summary": "...", "targets": [{"threat_level": "high"}]},
        "subscriber_agents": ["command_agent", "fire_control_agent"],
        "jamming_level": 0.7,
    },
    use_mock=False,  # 仍走 SAW（prefer_deterministic 默认 True）
)
assert out["route_mode"] == "mcdm_saw"
```

## 可选 PPO 路径

```bash
python scripts/train_anti_jam_mcdm_router.py --epochs 300
```

然后配置 `prefer_deterministic: false` 且存在 `ppo_channel_policy.meta.json` 时走 `ppo_channel_policy`。

## 命名对照

| 项 | 名称 |
|----|------|
| algorithm_id | `anti_jam_mcdm_router` |
| 类名 | `AntiJamMCDMRouter` |
| 默认 route_mode | `mcdm_saw` |
| 显示名 | Anti-Jam MCDM Router (SAW + optional PPO) |

## 文件

| 文件 | 职责 |
|------|------|
| `mcdm.py` | SAW 工业主路径 + provenance |
| `backend.py` | SAW / PPO 切换逻辑 |
| `agent/training/routing_env.py` | PPO 训练环境 |
| `scripts/train_anti_jam_mcdm_router.py` | 可选 PPO 训练入口 |
