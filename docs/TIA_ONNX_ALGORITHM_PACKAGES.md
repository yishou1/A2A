# TIA / 任务调度可转换 ONNX 算法包

对齐 `zsl/algorithmrepo` 的原生 `backend_type: onnx` 包规范：把可张量化的模型核导出为 `model.onnx`，与现有 `python_http_service` 兄弟包并存。

## 已导出包

| algorithm_id | 来源权重 | 输入 | 输出 | 兄弟 Python 服务 |
|---|---|---|---|---|
| `edl_evidential_verifier_onnx` | `edl_head.pt` | `features[1,6]` | `probability` / `epistemic` / `aleatoric` | `edl_evidential_verifier` |
| `odconv_confidence_refiner_onnx` | `odconv_refiner.pt` | `crops[1,3,128,128]` + `base_conf[1,1]` | `refined_confidence` | `battlefield_rtdetr_detector` |
| `supcon_meta_classifier_onnx` | `supcon_meta.pt` | `features[1,1024]` | `class_probabilities[1,4]` | `supcon_meta_classifier` |
| `marl_dynamic_router_onnx` | `marl_policy.pt` | `state[1,8]` | `channel_logits` / `reliability` | `marl_dynamic_router` |
| `marl_ppo_task_scheduler_onnx` | `marl_ppo_scheduler.pt` | `obs[1,102]` | `action_logits` / `value` | `marl_ppo_task_scheduler` |

决策侧（来自 zsl，本地 `models/*.onnx` 已存在）：

| algorithm_id | 模型 |
|---|---|
| `decision_plan_recommender_onnx` | `decision_planning_lr.onnx` |
| `compliance_risk_scorer_onnx` | `compliance_authorization_lr.onnx` |
| `target_trend_predictor_onnx` | `decision_planning_lstm.onnx` |

## 未转 ONNX（短期保持 Python service）

- RT-DETR 整网、MOTR、Mask2Former、ImageBind、Mamba、SynapseRAG、语义通信：依赖复杂预处理 / 动态结构 / 检索编排。

## 重新导出

```powershell
.\.venv\Scripts\python.exe scripts\export_tia_onnx_models.py
```

产物：

- `models/<name>.onnx`
- `examples/<algorithm_id>_onnx/1.0.0/`（含 card / schema / preprocess / postprocess / golden）

## 注册到 C++ 算法库（zsl algolib）

```powershell
.\build\Release\algolib.exe register .\examples\edl_evidential_verifier_onnx\1.0.0
.\build\Release\algolib.exe activate edl_evidential_verifier_onnx 1.0.0 onnx
# 其余包同理
```

调用时显式指定 `backend_type: "onnx"`。特征构造与业务编排仍由对应 `python_http_service` 负责。

## 说明

- EDL 的 `aleatoric` 在 ONNX 中使用类别熵近似，避免 Digamma 算子兼容问题。
- SupCon ONNX 使用固定原型，不含 request 时 support-shot 原型更新。
- MARL-PPO ONNX 只导出单 agent 的 actor/critic；多 agent 环境步进仍在 Python 服务。
