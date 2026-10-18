# Track Threat Algorithms Service

Python HTTP service for the track-threat algorithm packages.

Run:

```bash
pip install -r services/requirements.txt
python services/track_threat_algorithms/app/main.py
```

Default port: `9038`.

Mounted algorithm endpoints:

- `multimodal_feature_fuser` - M03 多模态融合
- `target_type_classifier` - M04 特征编码与分类
- `track_state_updater` - M05 多目标跟踪与定位
- `trajectory_predictor:1.0.0` - M16 时间序列预测模型（ST-GNN，与 LSTM 包区分）
- `graph_relation_reasoner:2.0.0` - M20 多帧 GRU-GNN（Python HTTP 门面 + ONNX Runtime）

Each mounted algorithm exposes:

```text
/<algorithm_id>/health
/<algorithm_id>/metadata
/<algorithm_id>/predict
```

Boundary: these packages are for simulation situation awareness only. KG/RAG, compliance authorization, planning, Enemy COA, weapon control, guidance, and engagement advice are outside this service.
