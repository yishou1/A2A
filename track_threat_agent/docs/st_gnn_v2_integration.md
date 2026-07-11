# ST-GNN v2 在线接入

## 模型配置

Agent 默认会查找内置模型目录：

```text
models/track_threat/st_gnn_aircraft_kaggle_v1_candidate
models/track_threat/st_gnn_ship_kaggle_v1
```

如果要替换为服务器训练出的新模型，再配置环境变量：

```text
ST_GNN_AIRCRAFT_MODEL_DIR
ST_GNN_SHIP_MODEL_DIR
ST_GNN_MODEL_DIR
ST_GNN_REQUIRED=false
ST_GNN_MAX_INFERENCE_MS=200
```

旧 `ST_GNN_MODEL_DIR` 作为单飞机模型兼容入口。Agent 主机启用 TorchScript 模型推理时额外安装 `requirements-model.txt`；不安装时服务仍可启动并回退到 Kalman / IMM / NumPy ST-GNN 链路。

## 加载校验

`TorchScriptBundleRunner` 依次检查：

1. `st_gnn_model_bundle/v2` schema。
2. object type、node/edge feature schema、历史长度和 horizon。
3. 所有文件 SHA256。
4. TorchScript 可加载性。
5. golden I/O 最大绝对误差不超过 `1e-4`。

任何一步失败都不会导致默认模式下服务崩溃。

## 在线构图

Agent 按模型 manifest 对 `TrackState.history_path` 重采样，生成与训练端一致的九维节点特征和八维边特征。只连接距离阈值内的同时态目标。模型输入为：

```text
history_features [N, 6, 9]
edge_index [2, E]
edge_features [E, 8]
physics_baseline [N, H, 2]
```

输出为二维残差、二维 `log_sigma` 和最终未来位置偏移。

## 输出和 AMOS

预测点新增：

```text
model_version
baseline_model
prediction_confidence
uncertainty_radius_m
inference_latency_ms
fallback_reason
```

这些字段随 artifact 中 `tracks[].predicted_path[]` 返回，也随 `track.updated.predicted_path` 回写 AMOS。Nacos 只发布发现信息、skills、Agent 状态和模型路径摘要，不传逐帧航迹。

当前内置模型状态：

- `st_gnn_ship_kaggle_v1`：release gate passed，可作为演示发布模型。
- `st_gnn_aircraft_kaggle_v1_candidate`：候选模型，FDE 和覆盖率通过，ADE 提升略低于 10% 门槛，用于演示和接口联调。

## 降级

- 历史不足：`fallback_reason=insufficient_history`
- 模型不可用：`fallback_reason=model_unavailable`
- 超过延迟阈值：`fallback_reason=inference_timeout`
- 推理异常：`fallback_reason=inference_error:*`

`ST_GNN_REQUIRED=false` 时 `/ready` 保持 true；设为 true 后，已配置模型不可用会令 `/ready=false`。

## 性能验收

在实际 Agent 部署主机执行：

```bash
PYTHONPATH=.. python scripts/benchmark_st_gnn_runtime.py \
  --model-dir /models/st_gnn_aircraft_v1 \
  --iterations 50 \
  --max-p95-ms 200
```

脚本固定使用 200 节点、2000 边，p95 超过 200 ms 时退出码为 1。
