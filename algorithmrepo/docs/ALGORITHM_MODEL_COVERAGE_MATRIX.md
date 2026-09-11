# M01–M20 算法模型覆盖矩阵

本文件以《18 类算法模型清单.md》的正文为准。虽然文件名写“18 类”，正文实际定义了
M01–M20 共 20 类模型。本矩阵是算法补齐工作的唯一状态基线；算法包存在、Mock 能返回、
或接口联调通过，都不能单独判定为“真实完成”。

## 完成判定

每一类模型至少要有一个实现同时满足以下条件，状态才能标记为 `COMPLETE`：

1. 有明确的 `algorithm_id`、Algorithm Card、输入/输出 Schema 和黄金样例。
2. 实现使用真实算法或经过训练/导出的模型产物，不使用 Mock、随机初始化结果或静默 fallback。
3. 模型产物记录来源、版本、SHA256、训练或导出方法及评价指标。
4. `/health` 返回 `model_loaded=true`，`/metadata` 身份与算法卡一致。
5. HTTP `/predict` 与 `algolib register/activate/run` 均通过真实模式验收。
6. 自动化测试通过，算法卡不再是 `draft`。

状态含义：

- `MISSING`：没有满足该类别定义的实现。
- `PARTIAL`：已有代码或包，但依赖、权重、真实推理或验收尚未闭环。
- `CALLABLE`：真实算法已经可以调用，但发布资料或生命周期状态尚未完成。
- `COMPLETE`：满足上述全部完成判定。

## 覆盖矩阵

| 编号 | 规定模型类别 | 当前候选实现 | 当前状态 | 已确认事实 | 下一步 |
|---|---|---|---|---|---|
| M01 | 聚类算法 | `clustering_engine` | `COMPLETE` | 已实现确定性 K-Means 与 DBSCAN；算法卡、Schema、两组 golden case、源码 SHA256、HTTP 与 `algolib` 严格真实验收均已通过 | 后续可按业务数据补充轮廓系数、ARI 等数据集评估指标 |
| M02 | 关联算法 | `execution_rule_matcher` | `COMPLETE` | 已修正规则置信度计算；固化 28 条 Apriori 风格规则，记录训练/独立留出集与源码/产物 SHA256；12 条留出场景动作和执行角色准确率均为 1.0；HTTP 与 `algolib` 严格真实验收通过 | 使用真实脱敏业务记录重新挖掘并做独立评估后再用于业务决策 |
| M03 | 线性回归模型 | `trajectory_linear_predictor` | `COMPLETE` | 已实现逐请求二维普通最小二乘回归，返回 4 个拟合系数和 R²；实现/评估集 SHA256 已校验；160 条合成留出轨迹的位置 MAE 为 0.519819、RMSE 为 0.964136；HTTP 与 `algolib` 严格真实验收通过 | 使用真实脱敏传感器航迹重新评估，并按机动类型与预测时域分层统计误差 |
| M04 | 逻辑回归模型 | `decision_plan_recommender_onnx`、`compliance_risk_scorer_onnx`；补充候选 `xbd_damage_assessor` | `COMPLETE` | 两个线性层 + Sigmoid 模型已用各 1200 条确定性参考数据重新训练并导出 ONNX；决策模型留出集 ROC AUC 0.960621，合规模型 0.995357；数据/脚本/模型 SHA256、Python ORT 与 C++ ORT 严格验收均已闭环。`target_trend_predictor_onnx` 经算子审计为 LSTM，仅归入 M16 | 使用真实脱敏业务标签重新训练；xBD 原始训练数据未随仓库提供，因此不作为本类 COMPLETE 的判定依据 |
| M05 | 随机森林模型 | `threat_priority_random_forest`、`threat_priority_random_forest_onnx` | `COMPLETE` | 已训练并固化 sklearn Random Forest，并新增原生 ONNX-ML 轻量包；源模型概率最大误差 1.43e-6，Python/C++ ORT 注册和 golden case 均通过 | 使用真实业务标注数据重新训练并独立评估后再用于业务决策 |
| M06 | 传统神经网络模型 | `supcon_meta_classifier`、`supcon_meta_classifier_onnx` | `COMPLETE` | 已训练并固化 99,200 参数投影 MLP 与类别原型；新增固定原型 ONNX 轻量版，与 PyTorch 概率最大误差 1.20e-7，并通过 Python/C++ ORT 真实验收；动态 support-shot 由完整服务版提供 | 使用真实脱敏融合嵌入重训和独立评估；当前合成聚类指标不得解释为实际目标识别精度 |
| M07 | 朴素贝叶斯网络 | `intent_gaussian_naive_bayes`、`intent_gaussian_naive_bayes_onnx` | `COMPLETE` | 已训练并固化 GaussianNB，并新增纯算术ONNX轻量包；源模型概率最大误差 5.11e-8，Python/C++ ORT 注册和 golden case 均通过 | 使用真实观测数据重训并完成概率校准、漂移监测与独立评估 |
| M08 | 生成对抗网络 | `conditional_tabular_gan`、`conditional_tabular_gan_onnx` | `COMPLETE` | 已完成条件 GAN 训练与生成器固化；新增显式噪声和 one-hot 条件 ONNX 轻量版，与 PyTorch 最大误差 1.20e-7，并通过 Python/C++ ORT；完整服务版负责种子、温度和命名字段 | 使用真实脱敏数据重训；合成输出不得冒充真实观测 |
| M09 | 大语言模型 | `llm_rule_explainer` | `PARTIAL` | 按外部 API 能力管理，不要求算法库内置或训练 LLM；算法包契约已存在 | 配置真实 API 凭据并完成超时、错误处理与端到端验收 |
| M10 | 检索增强生成模型 | `synapse_rag_retriever`、决策核心 RAG | `PARTIAL` | 按外部 RAG API 能力管理；Mock 链路可用，真实端点尚未完成验收 | 对真实 RAG API 完成检索结果、超时和降级策略验收 |
| M11 | 智能体模型 | `decision_planning_core` | `PARTIAL` | 按外部 Agent API 能力管理；服务包装存在，外部依赖尚未端到端闭环 | 配置真实 Agent API 并完成计划生成链路验收 |
| M12 | 联邦学习模型 | `federated_fedavg_aggregator` | `COMPLETE` | 已实现通用嵌套权重 FedAvg、客户端更新协议、三客户端八轮可复现实验、产物 SHA256、HTTP 与 `algolib` 严格验收 | 生产化仍需安全聚合、差分隐私、客户端认证和掉线恢复 |
| M13 | 强化学习模型 | `marl_ppo_task_scheduler`、`marl_ppo_task_scheduler_onnx`；补充候选 `marl_dynamic_router` | `COMPLETE` | 已训练并固化 39,434 参数共享 MARL-PPO Actor-Critic；新增动作掩码 ONNX 策略版，输出 logits、概率、动作和价值，与 PyTorch 最大误差 1.44e-6，并通过 Python/C++ ORT；完整服务版负责战场观测构造与跨智能体去重 | 使用真实脱敏任务调度轨迹离线评估及重训；当前合成场景指标不得解释为实战调度效果，`marl_dynamic_router` 仍需单独补权重 |
| M14 | 可解释 AI 模型 | `edl_evidential_verifier`、`edl_evidential_verifier_onnx`、规则证据链 | `COMPLETE` | 已训练并固化 290 参数 Dirichlet EDL 验证头；新增 ONNX 核心证据推理版，输出 evidence、alpha、概率和认知不确定性，与 PyTorch 最大误差 3.82e-6，并通过 Python/C++ ORT；完整服务版继续提供偶然不确定性、阈值理由和人工复核分流 | 使用真实脱敏检测标注进行跨传感器校准和漂移评估；当前合成指标不得解释为实际检测可靠性，人工复核必须回看源图像 |
| M15 | 多模态融合模型 | `multimodal_mamba_fusion`、`multimodal_mamba_fusion_onnx`；补充候选 `imagebind_multimodal_encoder` | `COMPLETE` | 已训练并固化 412,688 参数 Mamba 风格融合模型；新增四模态掩码 ONNX 轻量版，与 PyTorch 最大误差 5.97e-8，并通过 Python/C++ ORT；完整服务版支持不等长输入和 `sensor_id` 关联 | 当前 COMPLETE 范围是已编码向量的融合，不包含原始图像/SAR/雷达/文本编码；仍需离线打包 ImageBind 等预训练编码器，并用真实脱敏多模态配对数据重训评估 |
| M16 | 时间序列预测模型 | `target_trend_predictor_onnx`；补充候选 `trajectory_predictor` | `COMPLETE` | 已训练并固化 1,425 参数的单层 LSTM 与 Sigmoid 回归头，以 12 步归一化历史预测第 4 个未来步；800 条独立确定性合成留出序列上 MAE 0.036445、RMSE 0.051080、R² 0.980410，优于末值持久性基线 MAE 0.071554、RMSE 0.095422、R² 0.931635；训练/留出数据、脚本、ONNX 产物 SHA256 与 Python ORT、C++ ORT 严格验收均已闭环 | 使用真实脱敏且按时间隔离的传感器航迹重训，按目标类型、机动模式、预测时域和数据漂移分层评估；当前合成指标不得解释为实际预测精度 |
| M17 | 实时目标检测模型 | `battlefield_rtdetr_detector` | `PARTIAL` | 本机存在 `rtdetr-l.pt`，真实代码路径可运行；未确认战场微调权重 | 训练/验证 `battlefield_rtdetr.pt` 并记录检测指标 |
| M18 | 差分与变化检测模型 | `siamese_mask2former_damage`、`xbd_damage_assessor` | `PARTIAL` | 代码路径和 xBD 模型可运行；Siamese 模型产物与指标未闭环 | 固化 Mask2Former 权重和变化检测数据集评估 |
| M19 | 多目标跟踪与定位模型 | `motr_neural_kalman_tracker` | `PARTIAL` | 跟踪代码存在；MOTR/Kalman 正式 checkpoint 缺失 | 训练 VisDrone/DOTA 轨迹模型并做连续帧验收 |
| M20 | 图神经网络模型 | `graph_relation_reasoner`、`graph_relation_reasoner_onnx`；补充资产 ST-GNN 轨迹模型包 | `COMPLETE` | 已训练并固化 16,673 参数两层密集消息传递 GNN；新增十节点关系矩阵 ONNX 轻量版，与 PyTorch 最大误差 7.63e-6，并通过 Python/C++ ORT；完整服务版继续负责航迹特征、阈值、关系标签和连通编组 | 使用真实脱敏多目标关系标注重训，按目标类型、图密度和未见编组分层评估；当前合成指标不得解释为实际编组识别精度 |

## 当前可验证基线

- 算法包静态检查：当前共 43 个包；新增 M08/M13 ONNX 包严格静态与真实运行检查 2/2 通过。共 23 个算法卡为 `validated`，其余 20 个仍为 `draft`。
- Python HTTP 联调：23 个包的接口链路通过，其中 TIA 11 包此前使用 Mock 模式。
- TIA 独立核验：Mock 11/11；启用严格真实门禁后 6/11 通过（包含 M06 `supcon_meta_classifier`、M13 `marl_ppo_task_scheduler`、M14 `edl_evidential_verifier` 与 M15 `multimodal_mamba_fusion`）。其余算法因缺少
  checkpoint、`model_loaded=false`、ImageBind 本地资源缺失或 SynapseRAG 调用失败而被拒绝。
- 严格 HTTP/平台门禁：M01 聚类 1/1、M02 关联规则 1/1、M03 线性回归 1/1、M04 逻辑回归 ONNX 2/2、M05 随机森林 1/1、M06 传统神经网络 1/1、M07 朴素贝叶斯 1/1、M08 条件 GAN 1/1、M12 FedAvg 1/1、M13 强化学习 1/1、M14 可解释 AI 1/1、M15 多模态融合 1/1、M16 时间序列预测 1/1、M20 图神经网络 1/1、核心基础组 6/6、航迹威胁组 5/5、xBD 1/1 通过。
- ONNX Runtime：十二个 ONNX 包均通过 Python ORT 与 C++ ORT 双重真实验收；其中十一个为业务模型，
  `onnx_text_classifier` 已明确定位为常量 logits 契约夹具，不计作业务文本分类模型。

## 实施顺序

1. 启用真实模式验收门禁，禁止 Mock、stub、fallback 和缺权重启动。
2. 修复已有实现：TIA 权重与 ImageBind；LLM、RAG、Agent 按外部 API 边界验收。
3. CPU 可稳定实现的 M01、M02、M03、M04、M05、M06、M07、M08、M12、M13、M14、M15、M16、M20 缺口已经补齐。
4. 为 M09–M11 配置真实外部 API，并完成 LLM、RAG、Agent 端到端链路验收。
5. M08 条件 GAN 已完成参考数据训练闭环；下一步需要替换为真实脱敏数据并重新评估。
