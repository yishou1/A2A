# 动态算法库模型接入 SPEC v2

> 版本：v0.2  
> 范围：只设计“模型接入与算法注册运行层”，不设计训练、不设计自动 Pipeline 编排、不设计 fallback。  
> 主控语言：C++  
> 支持后端：`onnx` 与 `python_http_service`

---

## 1. 设计目标

本系统要实现一个可以被 C++ 主程序使用的算法库。算法库负责管理两类算法资产：

1. **ONNX Algorithm**
   - 用户提供 `.onnx` 模型文件与必要的 tokenizer、预处理、后处理、输入输出 schema、算法卡片。
   - C++ 主系统使用 ONNX Runtime 加载模型并执行本地推理。
   - 适合稳定、标准、高频、张量化的推理模型。

2. **Python HTTP Service Algorithm**
   - 用户提供一个已经启动或可部署的 Python HTTP 推理服务，以及算法卡片、输入输出 schema。
   - C++ 主系统作为 HTTP Client 调用 Python 服务的 `/predict` 接口。
   - 适合 LLM、Seq2Seq、RAG、多模态、动态图 GNN、复杂后处理等不适合直接 ONNX 化的模型。

系统必须支持：

- 算法注册
- 算法校验
- 算法启用
- 算法禁用
- 算法逻辑删除
- 算法卡片查询
- 指定算法执行
- 输入 schema 校验
- 输出 schema 校验
- 运行日志记录
- 为后续 Agent 提供算法卡片视图

系统不负责：

- 自动选择 ONNX 或 Python Service
- ONNX 失败后 fallback 到 Python Service
- Python Service 失败后 fallback 到 ONNX
- Pipeline 组合和动态编排
- 自动训练
- 联邦学习
- 持续学习
- 元学习
- 自动启动全部 Python 服务
- 自动加载全部模型

---

## 2. 核心原则

### 2.1 用户显式选择后端

调用方必须显式指定：

```json
{
  "algorithm_id": "text_entity_extractor",
  "version": "1.0.0",
  "backend_type": "onnx"
}
```

或：

```json
{
  "algorithm_id": "llm_rule_explainer",
  "version": "1.0.0",
  "backend_type": "python_http_service"
}
```

系统不自动判断哪个后端更优。

### 2.2 算法唯一键

算法运行资产的唯一键为：

```text
algorithm_key = algorithm_id + version + backend_type
```

允许同时存在：

```text
text_entity_extractor / 1.0.0 / onnx
text_entity_extractor / 1.0.0 / python_http_service
```

它们是两个不同资产。

### 2.3 失败即失败

模型接入或执行失败时，直接返回错误，不自动 fallback。

### 2.4 算法卡片是必须项

每个算法必须提供 `algorithm_card.yaml`。算法卡片同时服务于：

- 人类阅读
- 系统校验
- 后续 Agent 选择算法

### 2.5 Schema 是强制项

每个算法必须提供：

```text
input.schema.json
output.schema.json
```

执行前校验输入，执行后校验输出。

---

## 3. 术语

### 3.1 ONNX Algorithm

由 C++ 主系统直接加载 `.onnx` 文件并执行推理的算法资产。

典型流程：

```text
原始输入
  ↓
tokenizer / preprocess
  ↓
tensor
  ↓
ONNX Runtime 推理
  ↓
输出 tensor
  ↓
postprocess
  ↓
业务 JSON 输出
```

### 3.2 Python HTTP Service Algorithm

由外部 Python HTTP 服务承载模型，C++ 主系统通过 HTTP 调用。

典型流程：

```text
C++ Algorithm Library
  ↓ HTTP POST /predict
Python HTTP Service
  ↓ 模型推理
Python HTTP Service
  ↓ HTTP Response
C++ Algorithm Library
```

### 3.3 Tensor

Tensor 是模型真正输入和输出的多维数组，至少包含：

```text
name   : tensor 名称
dtype  : 数据类型，如 float32 / int64 / uint8
shape  : 维度，如 [1, 3, 640, 640]
data   : 二进制数据
```

示例：

```text
图像输入 tensor: [1, 3, 640, 640] float32
文本 input_ids: [1, 512] int64
Embedding 输出: [1, 1024] float32
```

### 3.4 Tokenizer

Tokenizer 是文本模型的前置处理器。它把自然语言字符串转成模型可读的 token id。

示例：

```text
"识别这段文本中的实体"
  ↓
input_ids:      [101, 1234, 5678, 102, 0, 0]
attention_mask: [1, 1, 1, 1, 0, 0]
```

常见 tokenizer 类型：

```text
wordpiece
bpe
sentencepiece
custom
```

### 3.5 图像预处理

图像预处理把图片文件或图片矩阵转成模型输入 tensor。

常见步骤：

```text
decode image
BGR/RGB 转换
resize / letterbox / crop
normalize
HWC 转 CHW
加 batch 维度
转 float32
```

### 3.6 后处理

后处理把模型输出 tensor 转成业务可读 JSON。

示例：

- 分类：logits → softmax → label / score
- NER：token logits → BIO 解码 → entity span
- 检测：boxes/scores → threshold → NMS → bbox JSON
- 分割：mask logits → threshold/argmax → mask_uri 或 polygon

---

## 4. 两种接入方式的模型区别

### 4.1 ONNX 适合的模型

ONNX 适合：

```text
输入输出稳定
主要是张量计算
没有复杂生成循环
没有强 Python 依赖
可以把 tokenizer / preprocess / postprocess 标准化
需要低延迟或高频调用
需要现场离线部署
```

典型任务：

```text
文本分类
标准 NER / Token Classification
Embedding
Reranker
目标检测
分割
变化检测
图像分类
固定窗口时序预测
轨迹回归
简单 policy network
部分传统 ML
```

选择 ONNX 的理由：

```text
C++ 直接调用
运行形态简单
部署依赖少
延迟可控
适合高频请求
适合封闭环境
```

### 4.2 Python HTTP Service 适合的模型

Python HTTP Service 适合：

```text
生成式模型
LLM
复杂 Seq2Seq
RAG
多模态模型
动态图 GNN
复杂多智能体推理
复杂 OCR 纠错
复杂规则/图谱推理
依赖 Python 生态的模型
```

选择 Python Service 的理由：

```text
保留 Python 生态
适合复杂推理逻辑
适合快速迭代
C++ 主程序与 Python 依赖隔离
服务可以独立部署、重启、扩容、升级
```

---

## 5. 推理任务族接入划分

| 任务族 | 推荐接入方式 | 原因 |
|---|---|---|
| 文本/结构化分类 | ONNX 优先 | 分类输入输出稳定，通常是 text/features → label/score。 |
| Define Threat | ONNX 或 Python Service | 简单分类走 ONNX；长上下文解释、规则推理、RAG 走 Python Service。 |
| ROE / 合规核查 | ONNX 或 Python Service | 固定规则分类可 ONNX；复杂规则解释、长文档理解、证据引用走 Python Service。 |
| 任务评估 | ONNX 或 Python Service | 评分模型可 ONNX；多因素推理和自然语言解释走 Python Service。 |
| Token 标注 / 信息抽取 | ONNX 优先 | 标准 NER、Token Classification、Span Extraction 适合 ONNX。 |
| OCR 后处理 | Python Service 优先 | OCR 纠错、格式修复、复杂清洗通常依赖 Python 或 LLM。 |
| CausalLM SFT | Python Service | CausalLM 涉及 tokenizer、KV cache、采样、停止条件、流式输出等复杂逻辑。 |
| 红方/蓝方/裁决智能体 | Python Service | 更像智能体/LLM 服务，不适合当作一次 ONNX 前向。 |
| 结构化指令生成 | Python Service | 属于生成任务，需要解码、约束生成、JSON 修复。 |
| 全链路解释 | Python Service | 解释生成依赖上下文组织和自然语言生成。 |
| Seq2SeqLM | Python Service 优先 | 摘要、改写、JSON 生成、规则解释通常需要解码逻辑。 |
| Embedding / 对比学习 | ONNX 优先 | 输出固定向量，高频调用，适合本地推理。 |
| Qwen-Embedding | ONNX 或 Python Service | 能稳定导出且 tokenizer 可 C++ 化时走 ONNX；否则服务化。 |
| ImageBind | Python Service 优先 | 多模态预处理复杂，Python 更稳。 |
| RAG Retriever | ONNX 优先 | query embedding / passage embedding 适合 ONNX；检索系统另行处理。 |
| SupCon | ONNX 优先 | 通常是特征提取或相似度模型，适合 ONNX。 |
| Reranker / 排序学习 | ONNX 优先 | Cross-Encoder 或打分模型输入输出稳定。 |
| 目标优先级 / 方案排序 / 候选方案评分 | ONNX 或 Python Service | 纯打分模型用 ONNX；LLM 解释性评分或复杂上下文用 Python Service。 |
| 目标检测 | ONNX 优先 | 检测模型是 ONNX 的典型场景。 |
| RT-DETR | ONNX 优先 | 模型主体适合 ONNX；后处理由 C++ 或配置化后处理实现。 |
| MOTR 检测部分 | ONNX 或 Python Service | 单帧检测可 ONNX；复杂跟踪和时序状态管理用 Python Service。 |
| 分割 / 变化检测 | ONNX 优先 | 图像 tensor → mask tensor，适合 ONNX。 |
| BDA / Mask2Former | ONNX 优先 | 模型主体适合 ONNX；复杂 GIS/遥感后处理可服务化。 |
| Confirm Impact | ONNX 或 Python Service | 纯视觉分类/分割走 ONNX；多模态解释走 Python Service。 |
| 图像分类 | ONNX 优先 | 输入输出稳定。 |
| 视频分类 | ONNX 或 Python Service | 固定帧数视频模型可 ONNX；长视频抽帧、事件理解、多模态分析用 Python Service。 |
| 时序预测 / 轨迹回归 | ONNX 优先 | 固定窗口时序输入 → 预测输出，适合 ONNX。 |
| Generate/Update Track | ONNX 或 Python Service | 单模型轨迹预测可 ONNX；复杂多目标数据关联和状态管理用 Python Service。 |
| Determine Time Available | ONNX 或 Python Service | 回归模型可 ONNX；规则、仿真、约束推理用 Python Service。 |
| Track Weapon | ONNX 或 Python Service | 纯轨迹回归可 ONNX；复杂跟踪系统服务化。 |
| 图学习 | Python Service 优先 | Dynamic GNN、ST-GNN、GNN Data Association 依赖 PyG/DGL、动态图采样和复杂结构。 |
| 固定图 GNN | ONNX 可选 | 图结构固定、输入张量固定时可尝试 ONNX。 |
| 强化学习 / 多智能体 RL | 分情况 | 单 policy network 适合 ONNX；多智能体仿真、环境交互、约束检查用 Python Service。 |
| PPO / MADDPG policy | ONNX 可选 | 只做 state → action score 时适合 ONNX。 |
| Safe RL / CMDP | Python Service 优先 | 约束、安全过滤、环境交互更适合服务化。 |
| GAN / 数据增强 | Python Service 优先 | 生成流程、采样、质量过滤、后处理多依赖 Python。 |
| 简单 generator | ONNX 可选 | latent → image 的固定前向可 ONNX。 |
| 传统 ML | ONNX 或 Python Service | Logistic、Linear、RandomForest 可导出 ONNX；复杂流程服务化。 |
| Association Rule / MADM / DBN / KG | Python Service 优先 | 规则推理、图谱查询、复杂决策流程不适合硬塞进 ONNX。 |

---

## 6. 算法生命周期

### 6.1 状态机

算法状态：

```text
draft
  ↓
validated
  ↓
active
  ↓
disabled
  ↓
deleted
```

含义：

- `draft`：已提交但未完成校验。
- `validated`：算法卡片、schema、模型文件或服务接口校验通过。
- `active`：可以被调用，也可以出现在 Agent 的候选算法卡片列表中。
- `disabled`：暂停调用，不出现在默认候选列表中。
- `deleted`：逻辑删除；不允许新调用，但保留历史记录。

### 6.2 逻辑删除优先

默认不做物理删除。物理清理使用单独 `purge` 操作，且需要管理员权限。

原因：

```text
历史审计需要保留 algorithm_id/version/backend_type
已保存结果可能引用旧算法
直接删除会破坏复现能力
```

---

## 7. 算法卡片规范

每个算法必须提供 `algorithm_card.yaml`。

### 7.1 顶层字段

```yaml
algorithm_id: text_entity_extractor
version: 1.0.0
display_name: Text Entity Extractor
backend_type: onnx
status: draft

task_family: token_extraction
modalities:
  input:
    - text
  output:
    - structured_json

capabilities:
  - named_entity_recognition
  - span_extraction
  - confidence_score
```

### 7.2 Agent Card

`agent_card` 给人类和后续 Agent 阅读。

```yaml
agent_card:
  summary: >
    从短文本或 OCR 后文本中抽取实体，输出实体文本、类型、位置和置信度。
  when_to_use:
    - 输入是文本
    - 需要抽取实体或字段
    - 输出需要结构化 JSON
  when_not_to_use:
    - 输入是原始图片
    - 需要长文档理解
    - 需要生成自然语言解释
  input_description: >
    text 字段为待抽取文本，建议长度不超过 512 tokens。
  output_description: >
    返回 entities 数组，每个实体包含 text、type、start、end、confidence。
  examples:
    - input:
        text: "张三于2024年5月到北京参加会议。"
      output:
        entities:
          - text: "张三"
            type: "PERSON"
          - text: "北京"
            type: "LOCATION"
```

### 7.3 Machine Spec

`machine_spec` 给系统执行和校验使用。

ONNX 示例：

```yaml
machine_spec:
  input_schema_ref: input.schema.json
  output_schema_ref: output.schema.json

  runtime:
    backend_type: onnx
    model_uri: model.onnx
    execution_provider: cpu

  tokenizer:
    type: wordpiece
    tokenizer_uri: tokenizer.json
    max_length: 512

  preprocess:
    config_uri: preprocess.yaml

  postprocess:
    config_uri: postprocess.yaml
    label_map_uri: label_map.json
```

Python HTTP Service 示例：

```yaml
machine_spec:
  input_schema_ref: input.schema.json
  output_schema_ref: output.schema.json

  runtime:
    backend_type: python_http_service
    endpoint: http://llm-rule-explainer:8080/predict
    health_endpoint: http://llm-rule-explainer:8080/health
    metadata_endpoint: http://llm-rule-explainer:8080/metadata
    timeout_ms: 10000
```

### 7.4 约束、性能和安全

```yaml
constraints:
  max_input_chars: 4000
  max_request_bytes: 10485760
  batch_supported: false
  streaming_supported: false

performance:
  latency_ms_p50: 40
  latency_ms_p95: 120
  primary_metric: entity_f1
  primary_score: 0.88

safety:
  risk_level: low
  requires_human_review: false
```

---

## 8. Agent 视图

给 Agent 的算法卡片列表必须是脱敏视图，只暴露：

```text
algorithm_id
version
backend_type
task_family
modalities
capabilities
agent_card.summary
agent_card.when_to_use
agent_card.when_not_to_use
agent_card.input_description
agent_card.output_description
input_schema summary
output_schema summary
constraints
safety.risk_level
safety.requires_human_review
```

不要暴露：

```text
内部鉴权 token
管理员字段
绝对路径
私有部署细节
内部网络凭据
```

---

## 9. ONNX 算法包规范

### 9.1 目录结构

```text
onnx_algorithms/
  text_entity_extractor/
    1.0.0/
      algorithm_card.yaml
      model.onnx
      input.schema.json
      output.schema.json
      tokenizer.json
      label_map.json
      preprocess.yaml
      postprocess.yaml
      golden_cases/
        case_001_input.json
        case_001_expected.json
      README.md
```

### 9.2 必需文件

```text
algorithm_card.yaml
model.onnx
input.schema.json
output.schema.json
preprocess.yaml
postprocess.yaml
golden_cases/
```

文本模型通常还需要：

```text
tokenizer.json
vocab.txt
merges.txt
special_tokens_map.json
label_map.json
```

视觉模型通常还需要：

```text
label_map.json
nms_config.yaml
normalization_config.yaml
```

### 9.3 第一版支持的 preprocess 类型

第一版建议只实现有限类型：

```text
no_op
text_tokenization
image_preprocess
tensor_from_json
```

不支持的复杂预处理应改走 Python HTTP Service。

### 9.4 第一版支持的 postprocess 类型

第一版建议只实现有限类型：

```text
no_op
classification_postprocess
token_classification_postprocess
embedding_postprocess
detection_postprocess
segmentation_postprocess
```

不支持的复杂后处理应改走 Python HTTP Service。

### 9.5 注册校验流程

ONNX 注册时必须：

1. 读取 `algorithm_card.yaml`。
2. 校验 `backend_type == onnx`。
3. 检查 `model.onnx` 存在。
4. 检查 `input.schema.json` 和 `output.schema.json` 存在且合法。
5. 检查 `preprocess.yaml` 和 `postprocess.yaml` 存在。
6. 尝试加载 ONNX 模型。
7. 检查输入输出 tensor 名称与卡片一致。
8. 执行至少一个 golden case。
9. 输出 schema 校验通过。
10. 状态更新为 `validated`。

---

## 10. Python HTTP Service 接入规范

### 10.1 角色定义

```text
C++ Algorithm Library = HTTP Client
Python HTTP Service   = HTTP Server
```

Python 不是发送端。C++ 在用户调用算法时向 Python 服务发送请求。

### 10.2 模型加载策略

第一版推荐且默认：

```text
服务启动时加载模型。
模型加载完成后，/health 返回 ready。
C++ 只有在 ready 后才调用 /predict。
```

不要默认懒加载。

原因：

```text
避免首个请求冷启动过慢
避免用户请求过程中才暴露加载失败
方便健康检查
方便资源预算
```

### 10.3 资源占用原则

```text
算法已注册 ≠ 服务已启动
算法已注册 ≠ 模型已加载
算法 active ≠ 服务 ready
```

资源只在服务实际启动并加载模型后占用。

系统不自动启动所有 Python 服务。部署系统或用户决定哪些服务运行。

### 10.4 Python Service 必须提供的接口

```text
GET  /health
GET  /metadata
POST /predict
```

### 10.5 GET /health

响应示例：

```json
{
  "ok": true,
  "status": "ready",
  "algorithm_id": "llm_rule_explainer",
  "version": "1.0.0",
  "model_loaded": true
}
```

允许状态：

```text
starting
loading
ready
degraded
error
```

调用规则：

```text
只有 status = ready 且 model_loaded = true 时，C++ 才允许调用 /predict。
```

### 10.6 GET /metadata

响应示例：

```json
{
  "algorithm_id": "llm_rule_explainer",
  "version": "1.0.0",
  "backend_type": "python_http_service",
  "task_family": "generation",
  "input_schema_version": "1.0.0",
  "output_schema_version": "1.0.0",
  "batch_supported": false,
  "streaming_supported": false,
  "max_input_chars": 20000,
  "timeout_ms_recommended": 10000
}
```

校验规则：

```text
algorithm_id 必须和算法卡片一致
version 必须和算法卡片一致
backend_type 必须是 python_http_service
```

### 10.7 POST /predict

请求格式：

```json
{
  "request_id": "req_001",
  "trace_id": "trace_001",
  "algorithm_id": "llm_rule_explainer",
  "version": "1.0.0",
  "inputs": {
    "task_text": "检查该任务是否符合规则。",
    "entities": []
  },
  "params": {
    "max_tokens": 512,
    "temperature": 0.2
  }
}
```

成功响应：

```json
{
  "ok": true,
  "request_id": "req_001",
  "trace_id": "trace_001",
  "algorithm_id": "llm_rule_explainer",
  "version": "1.0.0",
  "outputs": {
    "explanation": "该任务缺少必要字段，建议进入人工复核。",
    "confidence": 0.82
  },
  "usage": {
    "latency_ms": 1430,
    "input_tokens": 128,
    "output_tokens": 42
  },
  "error": null
}
```

失败响应：

```json
{
  "ok": false,
  "request_id": "req_001",
  "trace_id": "trace_001",
  "algorithm_id": "llm_rule_explainer",
  "version": "1.0.0",
  "outputs": {},
  "usage": {
    "latency_ms": 10023
  },
  "error": {
    "code": "MODEL_TIMEOUT",
    "message": "Model inference exceeded timeout."
  }
}
```

### 10.8 HTTP 状态码

```text
200 : HTTP 请求成功，业务结果看 ok 字段
400 : 请求格式错误
404 : algorithm_id 或 version 不匹配
422 : 输入 schema 校验失败
500 : 服务内部错误
503 : 服务未 ready 或模型未加载
504 : 推理超时
```

C++ 侧规则：

```text
HTTP 非 200，调用失败。
HTTP 200 但 ok=false，调用失败。
HTTP 200 且 ok=true，继续做 output_schema 校验。
```

### 10.9 大文件传输

第一版只支持 JSON 与 media_refs，不支持 multipart，不建议 base64 大文件。

大文件传：

```json
{
  "inputs": {
    "media_refs": [
      {
        "name": "input_video",
        "uri": "file:///data/videos/sample.mp4",
        "media_type": "video",
        "format": "mp4"
      }
    ],
    "question": "请判断该视频中的主要事件。"
  }
}
```

允许 URI 类型：

```text
file://
http://
https://
object storage URI
```

### 10.10 Python Service 算法包目录

```text
service_algorithms/
  llm_rule_explainer/
    1.0.0/
      algorithm_card.yaml
      input.schema.json
      output.schema.json
      service_contract.md
      golden_cases/
        case_001_request.json
        case_001_response.json
      README.md
```

服务代码可以单独放置：

```text
services/
  llm_rule_explainer/
    app/
      main.py
      model_loader.py
      predictor.py
      schemas.py
    requirements.txt
    Dockerfile
```

---

## 11. 对外 HTTP API

本节描述算法库本身可以暴露给上层系统的 HTTP API。也可以先实现 CLI，再实现 HTTP API。

### 11.1 注册算法

```http
POST /algorithms/register
```

注册 ONNX：

```json
{
  "backend_type": "onnx",
  "package_uri": "file:///model_repo/text_entity_extractor/1.0.0"
}
```

注册 Python Service：

```json
{
  "backend_type": "python_http_service",
  "card_uri": "file:///service_algorithms/llm_rule_explainer/1.0.0/algorithm_card.yaml"
}
```

响应：

```json
{
  "ok": true,
  "algorithm_id": "text_entity_extractor",
  "version": "1.0.0",
  "backend_type": "onnx",
  "status": "validated"
}
```

### 11.2 激活算法

```http
POST /algorithms/activate
```

请求：

```json
{
  "algorithm_id": "text_entity_extractor",
  "version": "1.0.0",
  "backend_type": "onnx"
}
```

### 11.3 禁用算法

```http
POST /algorithms/disable
```

请求：

```json
{
  "algorithm_id": "text_entity_extractor",
  "version": "1.0.0",
  "backend_type": "onnx"
}
```

### 11.4 删除算法

```http
DELETE /algorithms/{algorithm_id}/versions/{version}?backend_type=onnx
```

默认逻辑删除。

### 11.5 查询算法卡片

```http
GET /algorithms/cards
GET /algorithms/cards?task_family=generation&backend_type=python_http_service
```

返回 Agent 脱敏视图：

```json
[
  {
    "algorithm_id": "llm_rule_explainer",
    "version": "1.0.0",
    "backend_type": "python_http_service",
    "task_family": "generation",
    "capabilities": ["rule_explanation", "structured_summary"],
    "agent_card": {
      "summary": "根据输入规则、实体和分类结果生成可读解释。",
      "when_to_use": ["需要自然语言解释", "需要报告生成"],
      "when_not_to_use": ["只需要快速分类"]
    },
    "input_schema_ref": "input.schema.json",
    "output_schema_ref": "output.schema.json"
  }
]
```

### 11.6 执行算法

```http
POST /algorithms/run
```

请求：

```json
{
  "request_id": "req_001",
  "trace_id": "trace_001",
  "algorithm_id": "text_entity_extractor",
  "version": "1.0.0",
  "backend_type": "onnx",
  "inputs": {
    "text": "张三于2024年5月到北京参加会议。"
  },
  "params": {}
}
```

响应：

```json
{
  "ok": true,
  "request_id": "req_001",
  "trace_id": "trace_001",
  "algorithm_id": "text_entity_extractor",
  "version": "1.0.0",
  "backend_type": "onnx",
  "outputs": {
    "entities": []
  },
  "usage": {
    "latency_ms": 52
  },
  "error": null
}
```

---

## 12. CLI 建议

第一版可以先实现 CLI，便于开发和测试。

```bash
algolib register ./model_repo/text_entity_extractor/1.0.0
algolib validate text_entity_extractor --version 1.0.0 --backend onnx
algolib activate text_entity_extractor --version 1.0.0 --backend onnx
algolib disable text_entity_extractor --version 1.0.0 --backend onnx
algolib delete text_entity_extractor --version 1.0.0 --backend onnx
algolib list
algolib cards --task-family generation
algolib run text_entity_extractor --version 1.0.0 --backend onnx --input input.json
```

---

## 13. C++ 模块设计

```text
algolib/
  core/
    status.h
    error_code.h
    algorithm_key.h
    algorithm_card.h
    algorithm_entry.h
    algorithm_registry.h
    schema_validator.h

  runtime/
    algorithm_request.h
    algorithm_result.h
    algorithm_runner.h
    onnx_runner.h
    onnx_runner.cpp
    python_http_runner.h
    python_http_runner.cpp
    runtime_factory.h
    runtime_factory.cpp

  io/
    tensor_blob.h
    media_ref.h
    json_utils.h
    yaml_utils.h
    file_utils.h

  validation/
    onnx_package_validator.h
    python_service_validator.h
    golden_case_runner.h

  api/
    http_server.h
    algorithm_controller.h

  cli/
    main.cpp
```

### 13.1 核心枚举

```cpp
enum class BackendType {
    ONNX,
    PYTHON_HTTP_SERVICE
};

enum class AlgorithmStatus {
    DRAFT,
    VALIDATED,
    ACTIVE,
    DISABLED,
    DELETED
};
```

### 13.2 AlgorithmKey

```cpp
struct AlgorithmKey {
    std::string algorithm_id;
    std::string version;
    BackendType backend_type;
};
```

### 13.3 AlgorithmRequest

```cpp
struct AlgorithmRequest {
    std::string request_id;
    std::string trace_id;
    std::string algorithm_id;
    std::string version;
    BackendType backend_type;
    Json inputs;
    Json params;
};
```

### 13.4 AlgorithmResult

```cpp
struct AlgorithmResult {
    bool ok = false;
    std::string request_id;
    std::string trace_id;
    std::string algorithm_id;
    std::string version;
    BackendType backend_type;
    Json outputs;
    Json usage;
    Error error;
};
```

### 13.5 IAlgorithmRunner

```cpp
class IAlgorithmRunner {
public:
    virtual ~IAlgorithmRunner() = default;

    virtual Status Load(const AlgorithmEntry& entry) = 0;

    virtual AlgorithmResult Run(
        const AlgorithmRequest& request
    ) = 0;

    virtual HealthStatus HealthCheck() = 0;
};
```

### 13.6 RuntimeFactory

```cpp
class RuntimeFactory {
public:
    std::unique_ptr<IAlgorithmRunner> Create(BackendType backend_type);
};
```

---

## 14. 错误码

### 14.1 通用错误

```text
ALGORITHM_NOT_FOUND
ALGORITHM_NOT_ACTIVE
BACKEND_TYPE_MISMATCH
INPUT_SCHEMA_INVALID
OUTPUT_SCHEMA_INVALID
GOLDEN_CASE_FAILED
INVALID_ALGORITHM_CARD
```

### 14.2 ONNX 错误

```text
ONNX_MODEL_NOT_FOUND
ONNX_LOAD_FAILED
ONNX_RUNTIME_ERROR
ONNX_INPUT_TENSOR_MISMATCH
ONNX_OUTPUT_TENSOR_MISMATCH
PREPROCESS_FAILED
POSTPROCESS_FAILED
TOKENIZER_NOT_SUPPORTED
```

### 14.3 Python Service 错误

```text
SERVICE_NOT_READY
SERVICE_UNAVAILABLE
SERVICE_TIMEOUT
SERVICE_HTTP_ERROR
SERVICE_METADATA_MISMATCH
SERVICE_RESPONSE_INVALID
SERVICE_OUTPUT_SCHEMA_INVALID
```

---

## 15. 日志与审计

每次调用必须记录：

```json
{
  "request_id": "req_001",
  "trace_id": "trace_001",
  "algorithm_id": "text_entity_extractor",
  "version": "1.0.0",
  "backend_type": "onnx",
  "status": "success",
  "latency_ms": 52,
  "error_code": null,
  "input_hash": "sha256:...",
  "output_hash": "sha256:..."
}
```

高风险算法额外记录：

```text
safety.risk_level
safety.requires_human_review
confidence
human_review_status
```

---

## 16. 安全约束

本算法库只提供推理调用能力，算法输出应定位为辅助分析、仿真、审核或决策支持。

硬约束：

```text
不允许 Agent 直接执行未注册算法。
不允许调用未 active 的算法。
不允许调用未注册 endpoint。
不允许从 algorithm_card 之外临时指定 model_uri 或 endpoint。
高风险算法必须在算法卡片中声明 requires_human_review。
```

---

## 17. 第一版开发计划

### Phase 0：工程骨架

交付：

```text
CMake 工程
基本目录
Status / ErrorCode
BackendType / AlgorithmStatus
AlgorithmKey
Json/Yaml 工具
```

### Phase 1：算法卡片与 Registry

交付：

```text
AlgorithmCard 解析
AlgorithmEntry
AlgorithmRegistry
register / validate / activate / disable / delete / list
算法唯一键
逻辑删除
Agent card 脱敏视图
```

### Phase 2：Schema 校验

交付：

```text
input.schema.json 加载
output.schema.json 加载
执行前输入校验
执行后输出校验
基础 JSON Schema 支持
```

### Phase 3：Python HTTP Service Backend

交付：

```text
GET /health
GET /metadata
POST /predict
timeout
HTTP 状态码处理
response schema 校验
PythonServiceValidator
```

### Phase 4：ONNX Backend

交付：

```text
ONNX package validator
ONNX Runtime session wrapper
ONNX Runner 接口
no_op preprocess/postprocess
tensor_from_json
classification_postprocess
golden case
```

如果开发环境暂时没有 ONNX Runtime，可以先通过编译宏提供 stub，并保留接口。

### Phase 5：统一执行接口

交付：

```text
POST /algorithms/run 或 CLI run
RuntimeFactory
指定 backend 执行
日志记录
失败直接返回
```

### Phase 6：示例算法资产

交付：

```text
examples/onnx_text_classifier/
examples/python_http_service_llm_explainer/
examples/input_output_schema/
examples/golden_cases/
```

---

## 18. 外部参考

- ONNX Runtime Execution Providers: https://onnxruntime.ai/docs/execution-providers/
- ONNX Runtime C++ API: https://onnxruntime.ai/docs/get-started/with-cpp.html
- FastAPI Request Body: https://fastapi.tiangolo.com/tutorial/body/
- JSON Schema Documentation: https://json-schema.org/docs
