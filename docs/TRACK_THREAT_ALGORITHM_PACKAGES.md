# Track Threat Agent Algorithm Packages

This document records the five algorithm packages prepared for the Track Threat Agent in the A2A algorithm library. The package split follows `algorithm_integration_guide_for_juniors.md`, especially the class assignment for 表征、分类、跟踪、时序与图模型.

## Package Map

| Class | Algorithm package | Directory | Runtime endpoint |
|---|---|---|---|
| M03 多模态融合 | `multimodal_feature_fuser` | `examples/multimodal_feature_fuser/1.0.0` | `http://127.0.0.1:9022/multimodal_feature_fuser/predict` |
| M04 特征编码与分类 | `target_type_classifier` | `examples/target_type_classifier/1.0.0` | `http://127.0.0.1:9022/target_type_classifier/predict` |
| M05 多目标跟踪与定位 | `track_state_updater` | `examples/track_state_updater/1.0.0` | `http://127.0.0.1:9022/track_state_updater/predict` |
| M06 时间序列预测 | `trajectory_predictor` | `examples/trajectory_predictor/1.0.0` | `http://127.0.0.1:9022/trajectory_predictor/predict` |
| M07 图神经网络 | `graph_relation_reasoner` | `examples/graph_relation_reasoner/1.0.0` | `http://127.0.0.1:9022/graph_relation_reasoner/predict` |

## Service

All five packages are mounted by one Python HTTP service:

```text
services/track_threat_algorithms/app/main.py
```

Run:

```bash
python services/track_threat_algorithms/app/main.py
```

Service health:

```text
GET http://127.0.0.1:9022/health
```

Each mounted algorithm also exposes:

```text
GET /<algorithm_id>/health
GET /<algorithm_id>/metadata
POST /<algorithm_id>/predict
```

The metadata endpoint returns `algorithm_class`, `algorithm_class_name`, and `owner_scope=track_threat_agent` so the registry can distinguish these packages from decision/RAG/compliance packages.

## Boundary

These packages only cover structured situation-awareness algorithms:

- feature fusion
- object-type completion
- track state update
- trajectory prediction
- spatiotemporal graph relation/group detection

They intentionally do not include:

- KG+Transformer semantic reasoning
- RAG
- compliance authorization
- task planning
- Enemy COA or game-theory decision modeling
- weapon control, guidance, targeting, or engagement advice

Those capabilities belong to downstream decision, knowledge, or compliance agents.

