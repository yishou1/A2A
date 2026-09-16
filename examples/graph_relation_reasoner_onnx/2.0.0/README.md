# M20 多平台时空群体关系模型

`2.0.0` 使用 ONNX Runtime 在 CPU 上执行一个小型类型路由器和四个时空
GRU-GNN 专家模型：`aircraft`、`uav`、`ship`、`ground_vehicle`。路由器优先
读取 `type_name`，再兼容 `metadata.source_class`、`class_name` 和
`platform_type`。静态设施、未知词表或少于六个历史点的航迹会被明确拒绝，
由调用 Agent 按自身策略使用物理回退，不会臆造群体结论。

服务接口仍为 `graph_relation_reasoner`。在请求 `params` 中传入：

```json
{"model_variant": "m20_fused_onnx_v6", "relation_threshold": 0.5}
```

模型输入由服务内部构建：每个节点为最近 8 帧的相对位置、速度、航向、
高度和置信度；边包含相对距离、航向/速度一致性及其多帧统计量。输出为
`relations`、连通分量 `groups`、以及逐航迹 `routing` 记录。

本模型仅用于仿真态势的群体关系识别，不输出武器控制、攻击、制导或交战决策。
