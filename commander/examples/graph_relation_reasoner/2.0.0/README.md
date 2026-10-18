# M20 多平台时空群体关系模型

`2.0.0` 使用 ONNX Runtime 在 CPU 上执行一个小型类型路由器和四个时空
GRU-GNN 专家模型：`aircraft`、`uav`、`ship`、`ground_vehicle`。路由器优先
读取 `type_name`，再兼容 `metadata.source_class`、`class_name` 和
`platform_type`。静态设施、未知词表或少于六个历史点的航迹会被明确拒绝，
由调用 Agent 按自身策略使用物理回退，不会臆造群体结论。

服务接口为 `graph_relation_reasoner:2.0.0/python_http_service`，默认只执行 GRU-GNN。
旧版 `1.0.0` 单帧包保留供追溯，当前 HTTP 服务不接受其版本号，也不会在模型缺失时
偷偷切回旧模型。可在请求 `params` 中显式传入（省略时也默认使用时序版本）：

```json
{"model_variant": "m20_fused_onnx_v6", "relation_threshold": 0.5}
```

模型输入由服务内部构建：每个节点为最近 8 帧的相对位置、速度、航向、
高度和置信度；边包含相对距离、航向/速度一致性及其多帧统计量。输出为
`relations`、连通分量 `groups`、以及逐航迹 `routing` 记录。

本模型仅用于仿真态势的群体关系识别，不输出武器控制、攻击、制导或交战决策。

## 调用与依赖

本包来自 `wc/m20-graph-relation-onnx` 的提交
`dda1d1e116677048c998b643a6e4640d315e8307`，模型文件按 manifest 中的 SHA256 原样引入，
没有重新训练或更改权重。原分支目录名含 `_onnx`，但其卡片本身就是 Python HTTP 门面；
此处放到与 algorithm_id 一致的 `examples/graph_relation_reasoner/2.0.0` 下。

- 安装仓库现有 `services/requirements.txt` 即可；已包含 numpy、onnxruntime、FastAPI、uvicorn。
- 运行 `python services/track_threat_algorithms/app/main.py`，默认端口 9038。
- 原生接口：`GET /graph_relation_reasoner/health`、`GET /graph_relation_reasoner/metadata`、
  `POST /graph_relation_reasoner/predict`。
- 统一算法库入口为 `POST /run`，不是模型原生路由；先注册、激活本包，再调用。
- 每个专家最多 10 个节点，超过的节点在 `routing` 中返回 `expert_node_capacity_exceeded`，
  不会静默丢弃。每次推理至少需要同域两个合格节点。
- 历史不足、静态/未知类别是可解释的路由拒绝，返回 `ok=true` 和逐节点原因；Agent
  仅对这些明确拒绝的节点保留物理规则回退。模型正常判定无群体时，不再用本地规则覆盖。
- `groups` 及成员字段已写入输出 Schema。群组 `cohesion_score` 是组内关系边得分的均值。
- 原生服务非法业务输入沿用既有 `HTTP 200 + ok=false + error`；外层类型错误为 422，
  版本错误为 404，模型不可加载为 503。统一 /run 的 Schema 校验错误为 422。
- 模型报告仍是原分支候选模型的报告；接口验证不等于在生产数据上的精度验收。
