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
| `battlefield_rtdetr_detector` | 9020 | RT-DETR+ODConv | M01 |
| `siamese_mask2former_damage` | 9021 | Siamese Mask2Former | M02 |
| `edl_evidential_verifier` | 9022 | EDL | M17 |
| `motr_neural_kalman_tracker` | 9023 | MOTR+Kalman | M05 |
| `marl_ppo_task_scheduler` | 9024 | MARL-PPO 调度 | M15 |
| `imagebind_multimodal_encoder` | 9025 | ImageBind | M03/M04 |
| `multimodal_mamba_fusion` | 9026 | Multimodal Mamba | M03 |
| `supcon_meta_classifier` | 9027 | SupCon+Meta | M04 |
| `synapse_rag_retriever` | 9028 | SynapseRAG | M13 |
| `knowledge_semantic_comm` | 9029 | Semantic Comm | M12 |
| `marl_dynamic_router` | 9030 | MARL 路由 | M15 |

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

## Python 测试

```powershell
pip install -r services/requirements.txt
pip install pytest httpx
pytest tests/python/test_tia_algorithm_services.py -q
```
