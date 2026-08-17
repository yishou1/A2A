# TIA 战术情报 Agent 算法包接入说明

> 完整架构与分层职责说明见：[TIA_ALGORITHM_PACKAGE_ARCHITECTURE.md](./TIA_ALGORITHM_PACKAGE_ARCHITECTURE.md)

本分支 `feat/tia-algorithm-packages` 基于师兄算法库规范，将 TIA 三技能流水线中的 **11 个子算法** 打包为 `python_http_service` 算法包。

规范来源（`origin/zsl/algorithmrepo` 分支）：

- `algorithm_integration_guide_for_juniors.md`
- `algorithm_library_model_integration_SPEC.md`
- `algorithm_library_flows.md`

## 算法清单

| algorithm_id | 端口 | TIA 模块 | M 编号 |
|---|---:|---|---|
| `battlefield_rtdetr_detector` | 9020 | RT-DETR+ODConv | M17 |
| `siamese_mask2former_damage` | 9021 | Siamese Mask2Former | M18 |
| `edl_evidential_verifier` | 9022 | EDL | M14 |
| `motr_neural_kalman_tracker` | 9023 | MOTR+Kalman | M19 |
| `marl_ppo_task_scheduler` | 9024 | MARL-PPO 调度 | M13 |
| `imagebind_multimodal_encoder` | 9025 | ImageBind | M15 |
| `multimodal_mamba_fusion` | 9026 | Multimodal Mamba | M15 |
| `supcon_meta_classifier` | 9027 | SupCon+Meta | M06 |
| `synapse_rag_retriever` | 9028 | SynapseRAG | M10 |
| `knowledge_semantic_comm` | 9029 | Semantic Comm | 工程辅助（不等于 M12 FedAvg） |
| `marl_dynamic_router` | 9030 | MARL 路由 | M13 |

## 目录结构

每个算法包位于：

```text
examples/<algorithm_id>/1.0.0/
  algorithm_card.yaml
  input.schema.json
  output.schema.json
  golden_cases/case_001_request.json
  golden_cases/case_001_response.json
  README.md
  service_contract.md

services/<algorithm_id>/app/main.py
services/a2a_algorithms_common/tia_predictors.py   # 推理封装
agent/                                            # TIA 原始算法实现
```

## 生成 / 更新算法包

```powershell
$env:TIA_USE_MOCK="1"
python scripts/bootstrap_tia_algorithm_packages.py
```

## 启动 TIA 算法服务

```powershell
# 仅启动 TIA 11 个服务
./scripts/start_a2a_algorithm_services.ps1 -TiaOnly

# 启动全部（原有 7 个 + TIA 11 个）
./scripts/start_a2a_algorithm_services.ps1
```

## 注册 / 激活 / 运行（algolib）

```powershell
cmake -S . -B build
cmake --build build

./build/algolib.exe register ./examples/marl_ppo_task_scheduler/1.0.0
./build/algolib.exe activate marl_ppo_task_scheduler 1.0.0 python_http_service
./build/algolib.exe show-card marl_ppo_task_scheduler 1.0.0 python_http_service
./build/algolib.exe run ./examples/marl_ppo_task_scheduler/1.0.0/golden_cases/case_001_request.json
```

## 环境变量

| 变量 | 说明 |
|------|------|
| `TIA_USE_MOCK=1` | 使用 mock 推理（默认，无需 GPU 权重） |
| `TIA_USE_MOCK=0` | 加载真实模型权重（需 `models/checkpoints/`） |
| `PORT` | 各服务监听端口 |

## M06 SupCon Meta 真实模型

`supcon_meta_classifier` 的 small profile 已提供经过训练的确定性
`models/checkpoints/supcon_meta_s.safetensors`。真实模式会核对模型元数据、输入维度与
SHA256；权重缺失或不匹配时服务会拒绝报告 `model_loaded=true`，不会以随机初始化网络
代替正式模型。

如需从仓库内的确定性合成参考嵌入重新训练：

```powershell
python scripts/train_supcon_meta_classifier.py
```

该参考数据仅用于验证训练、固化、加载和调用链路，不代表真实业务目标识别精度。
`scripts/download_models.py --heads-only` 生成的随机初始化神经网络头只适合开发冒烟，
不能作为模型验收产物。

真实 HTTP 与 `algolib` 门禁：

```powershell
./scripts/run_algorithm_batch_acceptance.ps1 `
  -Group m06-neural-network `
  -Python python `
  -Algolib ./build-smoke-offline/Debug/algolib.exe `
  -RealGate
```

## M13 MARL-PPO 真实模型

`marl_ppo_task_scheduler` 的 small profile 使用经过 PPO 训练的
`models/checkpoints/marl_ppo_scheduler_s.safetensors`。策略网络共享
Actor-Critic 参数，同时编码智能体角色、同类资源索引和可用性，并对无效目标、不可用资源
及重复分配执行动作掩码。真实模式会核对权重 SHA256 和环境维度；缺失或不匹配时不会加载
随机策略。

重新训练和评估：

```powershell
python scripts/train_marl_ppo_scheduler.py
```

训练脚本生成固定种子的合成训练场景和 256 个独立留出场景，并将训练策略与受约束随机
策略进行对比。该数据仅验证 RL 工程闭环，不代表实战调度表现。

真实 HTTP 与 `algolib` 门禁：

```powershell
./scripts/run_algorithm_batch_acceptance.ps1 `
  -Group m13-reinforcement-learning `
  -Python python `
  -Algolib ./build-smoke-offline/Debug/algolib.exe `
  -RealGate
```

## M14 EDL 真实模型与证据链

`edl_evidential_verifier` 的 small profile 使用
`models/checkpoints/edl_head_s.safetensors`。该模型以 Dirichlet evidence
形式输出验证概率、认知不确定性和偶然不确定性，并为每个候选保留特征、类别证据、
Dirichlet 参数和阈值判断理由。服务不会丢弃失败项，而是将全部结果明确分流到
`verified_detections`、`rejected_detections` 和 `review_queue`。

重新训练及校准评估：

```powershell
python scripts/train_edl_evidential_verifier.py
```

人工复核项不能进入后续已验证检测流。复核人员需要回看源图像和边界框，并结合返回的
证据与不确定性决定接受或拒绝。当前参考数据为合成检测质量标签，只用于验证工程与校准链路。

真实 HTTP 与 `algolib` 门禁：

```powershell
./scripts/run_algorithm_batch_acceptance.ps1 `
  -Group m14-explainable-ai `
  -Python python `
  -Algolib ./build-smoke-offline/Debug/algolib.exe `
  -RealGate
```

## M15 多模态 Mamba 融合真实模型

`multimodal_mamba_fusion` 的 small profile 使用
`models/checkpoints/mamba_fusion_s.safetensors`。模型接收已经由上游编码器产生的
多模态向量，将每个向量补齐或截断到 256 维，经 Mamba 风格状态空间模块融合后输出
单位归一化向量。存在 `track.sensor_id` 时，航迹只和指定模态的局部表示关联，不再依赖
输入字典的偶然顺序。

当前实现完成的是“嵌入级多模态融合”，不负责把原始图像、SAR、雷达或文本编码成向量；
`imagebind_multimodal_encoder` 所需的本地预训练资源仍需单独打包。

确定性训练与留出集评估：

```powershell
python scripts/train_multimodal_mamba_fusion.py
```

真实 HTTP 与 `algolib` 门禁：

```powershell
./scripts/run_algorithm_batch_acceptance.ps1 `
  -Group m15-multimodal-fusion `
  -Python python `
  -Algolib ./build-smoke-offline/Debug/algolib.exe `
  -RealGate
```

## Python 测试

```powershell
pip install -r services/requirements.txt
pip install pytest httpx
pytest tests/python/test_tia_algorithm_services.py -q
```
