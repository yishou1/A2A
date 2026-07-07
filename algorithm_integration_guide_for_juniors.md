# 算法接入指南（面向算法接入任务分工）

本文档用于指导后续将 18 类算法能力接入当前 A2A 算法库。请先理解一个核心原则：

当前算法库不是训练框架，也不是算法源码仓库；它是算法能力的注册、校验、统一调用和对外暴露层。

一个算法要接入算法库，本质上是把它包装成以下两种形式之一：

```text
1. onnx
   适合输入输出规整、可导出为 ONNX 的模型推理。

2. python_http_service
   适合 Python 算法、目标检测、RAG、LLM、规则推理、强化学习策略、聚类、排序、复杂服务。
```

## 1. 接入目标

每位同学负责若干类算法，最终交付的不是论文调研结论，而是一个可以被算法库注册、激活、调用的算法包。

每个算法包至少需要做到：

```text
可以 register
可以 activate
可以 run
输入符合 input.schema.json
输出符合 output.schema.json
算法卡片 algorithm_card.yaml 描述清楚
至少提供 1 个 golden case
```

## 2. 当前算法库能做什么

算法库已经支持：

```text
注册算法包
校验算法卡片
校验输入 schema
校验输出 schema
管理算法状态
通过 CLI 调用算法
通过 HTTP Server 调用算法
记录执行审计日志
```

算法库不负责：

```text
训练模型
自动下载模型
自动启动 Python 服务
自动选择算法
自动 fallback
自动理解任意 ONNX 模型输入输出
```

所以接入任务的重点是：把具体算法整理成算法库能理解的标准接口。

## 3. 18 类算法推荐接入方式

| 编号 | 算法类型 | 推荐方式 | 优先级 | 说明 |
|---|---|---|---|---|
| M01 | 实时目标检测 | python_http_service | 第二批 | YOLO、RT-DETR 等可服务化，ONNX 也可行但图像后处理复杂 |
| M02 | 差分检测与变化检测 | python_http_service | 第二批 | 输入通常是前后图像或前后状态 |
| M03 | 多模态融合 | python_http_service | 第三批 | 多输入、多模型，建议服务化 |
| M04 | 特征编码与分类 | onnx 或 python_http_service | 第一批 | 输入输出简单，适合作为标准模型接入样例 |
| M05 | 多目标跟踪与定位 | python_http_service | 第二批 | 通常需要维护轨迹状态 |
| M06 | 时间序列预测 | onnx 或 python_http_service | 第一批 | 适合航迹预测、状态趋势预测 |
| M07 | 图神经网络 | python_http_service | 第二批 | 图结构输入更适合 Python 处理 |
| M08 | 聚类与数据关联 | python_http_service | 第一批 | K-Means、DBSCAN 很容易服务化 |
| M09 | 回归与评分 | onnx 或 python_http_service | 第一批 | 最适合优先接入 |
| M10 | 随机森林与集成学习 | python_http_service | 第一批 | sklearn、xgboost 服务化简单 |
| M11 | 多属性决策与排序 | python_http_service | 第一批 | 可先用规则或 MADM 实现 |
| M12 | 大语言模型 | python_http_service | 第二批 | C++ 算法库只调用 LLM 服务 |
| M13 | 检索增强生成 | python_http_service | 第二批 | 检索、向量库、重排放在 Python 服务内 |
| M14 | 智能体 | python_http_service | 第三批 | 更像流程编排器，不建议第一批做 |
| M15 | 强化学习 | python_http_service | 第二批 | 接策略推理，不接训练过程 |
| M16 | 联邦学习 | python_http_service | 第三批 | 更像分布式训练/聚合机制 |
| M17 | 可解释 AI 与证据推理 | python_http_service | 第二批 | 可接 SHAP、证据链、解释器 |
| M18 | 规则/知识图谱与神经符号推理 | python_http_service | 第二批 | 规则引擎、图谱查询服务化 |

第一阶段建议优先完成：

```text
M09 回归与评分
M11 多属性决策与排序
M08 聚类与数据关联
M10 随机森林与集成学习
M04 特征编码与分类
M06 时间序列预测
```

这些算法输入输出主要是结构化 JSON，最容易形成完整闭环。

## 4. 四人任务分工建议

### 同学 A：结构化评分与排序类

负责：

```text
M09 回归与评分
M11 多属性决策与排序
```

建议算法包：

```text
mission_success_scorer
threat_priority_ranker
```

目标：

```text
输入候选目标、任务特征、约束指标
输出评分、等级、排序结果、解释字段
```

### 同学 B：聚类、关联与集成学习类

负责：

```text
M08 聚类与数据关联
M10 随机森林与集成学习
```

建议算法包：

```text
observation_clusterer
situation_ensemble_evaluator
```

目标：

```text
输入多源观测或多维特征
输出聚类 ID、关联关系、态势评估结果
```

### 同学 C：分类、时序与跟踪类

负责：

```text
M04 特征编码与分类
M06 时间序列预测
M05 多目标跟踪与定位
```

建议算法包：

```text
target_type_classifier
trajectory_predictor
track_state_updater
```

目标：

```text
输入目标特征、历史轨迹、观测序列
输出类别概率、未来轨迹点、状态估计
```

### 同学 D：知识增强、规则推理与大模型类

负责：

```text
M12 大语言模型
M13 检索增强生成
M17 可解释 AI 与证据推理
M18 规则/知识图谱与神经符号推理
```

建议算法包：

```text
intent_parser_llm
rule_rag_retriever
evidence_explainer
roe_rule_checker
```

目标：

```text
输入任务文本、规则、证据、上下文
输出结构化解释、规则命中、风险提示、证据链
```

M01、M02、M03、M14、M15、M16 可作为第二阶段或第三阶段任务。

## 5. 每个算法包的标准目录

Python HTTP Service 算法包目录：

```text
examples/<algorithm_id>/<version>/
  algorithm_card.yaml
  input.schema.json
  output.schema.json
  README.md
  service_contract.md
  golden_cases/
    case_001_request.json
    case_001_response.json
```

ONNX 算法包目录：

```text
examples/<algorithm_id>/<version>/
  algorithm_card.yaml
  model.onnx
  input.schema.json
  output.schema.json
  preprocess.yaml
  postprocess.yaml
  tensor_contract.yaml
  label_map.json
  README.md
  golden_cases/
    case_001_input.json
    case_001_expected.json
```

第一阶段优先建议使用 `python_http_service`，因为真实算法大概率是 Python 生态里的模型或规则服务。

## 6. Python HTTP Service 接入模板

### 6.1 algorithm_card.yaml

```yaml
algorithm_id: mission_success_scorer
version: 1.0.0
display_name: Mission Success Scorer
backend_type: python_http_service
status: draft

task_family: scoring
modalities:
  input:
    - structured_json
  output:
    - structured_json

capabilities:
  - success_score
  - risk_score

agent_card:
  summary: >
    Score a structured task candidate and return success probability.
  when_to_use:
    - A structured candidate needs feasibility scoring.
  when_not_to_use:
    - Raw image or video detection is required.
  input_description: >
    The request inputs contain numeric task features.
  output_description: >
    Returns score, level and confidence.

machine_spec:
  input_schema_ref: input.schema.json
  output_schema_ref: output.schema.json
  runtime:
    backend_type: python_http_service
    endpoint: http://127.0.0.1:9001/predict
    health_endpoint: http://127.0.0.1:9001/health
    metadata_endpoint: http://127.0.0.1:9001/metadata
    timeout_ms: 3000

constraints:
  max_input_chars: 10000
  max_request_bytes: 1048576
  batch_supported: false
  streaming_supported: false

performance:
  latency_ms_p50: 20
  latency_ms_p95: 100
  primary_metric: auc
  primary_score: 0.85

resource_requirements:
  min_cpu_cores: 2
  recommended_cpu_cores: 4
  min_memory_mb: 4096
  recommended_memory_mb: 8192
  min_gpu_count: 0
  gpu_type: optional
  min_vram_mb: 0
  recommended_vram_mb: 0
  disk_mb: 2048

model_profile:
  parameter_count: 110000000
  parameter_count_text: 110M
  flops: 22000000000
  flops_text: 22 GFLOPs
  flops_input_shape: [1, 3, 640, 640]
  model_size_mb: 420
  precision: fp32

safety:
  risk_level: medium
  requires_human_review: true
```

### 6.2 input.schema.json

```json
{
  "type": "object",
  "required": ["features"],
  "properties": {
    "features": {
      "type": "object",
      "required": ["distance", "speed", "quality"],
      "properties": {
        "distance": { "type": "number", "minimum": 0 },
        "speed": { "type": "number", "minimum": 0 },
        "quality": { "type": "number", "minimum": 0, "maximum": 1 }
      },
      "additionalProperties": true
    }
  },
  "additionalProperties": false
}
```

### 6.3 output.schema.json

```json
{
  "type": "object",
  "required": ["score", "level", "confidence"],
  "properties": {
    "score": { "type": "number", "minimum": 0, "maximum": 1 },
    "level": { "type": "string", "enum": ["low", "medium", "high"] },
    "confidence": { "type": "number", "minimum": 0, "maximum": 1 }
  },
  "additionalProperties": true
}
```

### 6.4 Python 服务必须提供的接口

```text
GET  /health
GET  /metadata
POST /predict
```

`GET /health` 返回：

```json
{
  "ok": true,
  "status": "ready",
  "algorithm_id": "mission_success_scorer",
  "version": "1.0.0",
  "model_loaded": true
}
```

`GET /metadata` 返回：

```json
{
  "algorithm_id": "mission_success_scorer",
  "version": "1.0.0",
  "backend_type": "python_http_service"
}
```

`POST /predict` 输入是完整的 AlgorithmRequest：

```json
{
  "request_id": "req_001",
  "trace_id": "trace_001",
  "algorithm_id": "mission_success_scorer",
  "version": "1.0.0",
  "backend_type": "python_http_service",
  "inputs": {
    "features": {
      "distance": 12.5,
      "speed": 3.2,
      "quality": 0.87
    }
  },
  "params": {}
}
```

`POST /predict` 返回：

```json
{
  "ok": true,
  "request_id": "req_001",
  "trace_id": "trace_001",
  "algorithm_id": "mission_success_scorer",
  "version": "1.0.0",
  "outputs": {
    "score": 0.76,
    "level": "medium",
    "confidence": 0.91
  },
  "usage": {
    "latency_ms": 12
  },
  "error": null
}
```

## 7. ONNX 接入模板

只有当模型满足以下条件时，优先考虑 ONNX：

```text
模型可以导出 model.onnx
输入 tensor 名称、dtype、shape 明确
输出 tensor 名称、dtype、shape 明确
预处理和后处理能用当前支持的 adapter 表达
```

当前支持的 preprocess：

```text
no_op
tensor_from_json
text_tokenization
json_to_tensor_map
```

当前支持的 postprocess：

```text
no_op
classification_postprocess
raw_tensor_to_json
```

如果模型需要复杂图像预处理、NMS、特征库查询、状态维护，优先改走 `python_http_service`。

## 8. 注册、激活、运行验收命令

构建项目：

```powershell
cmake -S . -B build
cmake --build build
```

注册算法：

```powershell
.\build\algolib.exe register .\examples\<algorithm_id>\1.0.0
```

激活算法：

```powershell
.\build\algolib.exe activate <algorithm_id> 1.0.0 python_http_service
```

查看算法：

```powershell
.\build\algolib.exe show-card <algorithm_id> 1.0.0 python_http_service
```

运行算法：

```powershell
.\build\algolib.exe run request.json
```

启动 HTTP Server：

```powershell
.\build\algolib_server.exe --host 127.0.0.1 --port 8088
```

HTTP 调用：

```powershell
curl -X POST http://127.0.0.1:8088/run `
  -H "Content-Type: application/json" `
  -d "@request.json"
```

## 9. 每位同学的交付清单

每接入一个算法，必须提交：

```text
1. 算法包目录
2. algorithm_card.yaml
3. input.schema.json
4. output.schema.json
5. golden_cases
6. README.md
7. 如果是 Python HTTP Service，提供服务启动说明
8. 一份 request.json 示例
9. 注册、激活、运行截图或日志
10. resource_requirements 资源需求字段
11. model_profile 参数量、FLOPs、模型大小和精度字段
```

验收标准：

```text
algolib register 成功
algolib activate 成功
algolib list 能看到算法
algolib show-card 能看到 agent_view
algolib run 能返回 ok=true
输出字段符合 output.schema.json
失败时能返回清晰 error
```

## 10. 常见错误

### 10.1 注册失败

常见原因：

```text
algorithm_card.yaml 字段缺失
backend_type 写错
schema 文件路径写错
Python 服务没有启动
/health 返回格式不符合要求
```

### 10.2 激活失败

常见原因：

```text
算法还没有 validated
注册阶段校验未通过
algorithm_id、version、backend_type 写错
```

### 10.3 运行失败

常见原因：

```text
算法没有 active
request.json 中 backend_type 不匹配
inputs 不符合 input.schema.json
Python /predict 返回格式不符合 AlgorithmResult
outputs 不符合 output.schema.json
```

### 10.4 ONNX 模型失败

常见原因：

```text
默认构建是 stub，不是真实 ONNX Runtime
模型 tensor 名称和 tensor_contract.yaml 不一致
preprocess 输出 tensor 和模型输入不一致
postprocess 期望 tensor 和模型输出不一致
```

## 11. 选型原则

优先选择能快速跑通闭环的算法，不要一开始追求复杂模型。

第一批建议：

```text
M09 回归与评分
M11 排序决策
M08 聚类与关联
M10 集成学习
M04 分类
M06 时间序列预测
```

第二批建议：

```text
M01 目标检测
M02 变化检测
M05 跟踪定位
M12 大语言模型
M13 RAG
M17 可解释 AI
M18 规则推理
M15 强化学习策略推理
```

第三批建议：

```text
M03 多模态融合
M14 智能体
M16 联邦学习
```

接入时始终记住：

```text
算法库只要求统一输入、统一输出、统一注册、统一调用。
算法内部怎么实现，可以先简单，后续再替换成真实模型。
```
