/* Frontend-only display catalog derived from the current A2A implementation. */

window.A2ACapabilityCatalog = [
  {
    id: "cognition", name: "多模态感知与认知", agent: "tactical_intelligence_agent",
    description: "处理图像和传感器帧，完成检测、识别、跨模态融合与情报分发。",
    algorithms: [
      ["RT-DETR + ODConv", "实时目标检测与动态卷积特征提取。"],
      ["Siamese Mask2Former", "对比前后帧，定位目标毁伤区域。"],
      ["EDL 证据深度学习", "验证检测结果并估计不确定性。"],
      ["MOTR + Neural Kalman", "多目标关联、跟踪与状态估计。"],
      ["ImageBind Cross-Modal", "把多源传感器数据编码到统一表征空间。"],
      ["Multimodal Mamba", "融合不同模态的时序特征。"],
      ["SupCon + Meta-Learning", "基于小样本完成目标类别判定。"],
      ["SynapseRAG", "检索知识库并补充识别依据。"],
      ["Knowledge Semantic Comm", "提取任务相关语义，压缩通信内容。"],
      ["MARL Dynamic Routing", "按链路状态选择动态传输路径。"],
    ],
  },
  {
    id: "tracking", name: "航迹生成与维护", agent: "track_threat_agent",
    description: "关联连续观测，更新目标状态并预测多模型运动轨迹。",
    algorithms: [
      ["门控最近邻 + Kalman", "完成观测关联和航迹状态更新。"],
      ["自适应运动模型", "按目标运动特征选择预测模型。"],
      ["IMM 多模型预测", "融合匀速、匀加速和协调转弯预测。"],
      ["ST-GNN 轨迹修正", "利用邻接目标关系修正轨迹。"],
    ],
  },
  {
    id: "threat_assessment", name: "威胁评估与排序", agent: "track_threat_agent",
    description: "按目标、编组和受保护资产影响计算威胁排序及证据链。",
    algorithms: [
      ["多因子威胁评分", "综合距离、速度、意图等因素计算威胁值。"],
      ["DBN 后验平滑", "对连续帧威胁概率进行平滑。"],
      ["XAI 证据链", "记录威胁排序的特征贡献与依据。"],
      ["航向速度凝聚编组", "按距离、航向和速度识别目标编组。"],
      ["编组注意力评分", "融合成员威胁得到编组级评分。"],
      ["资产影响评分", "按接近距离和闭合速度评估资产风险。"],
    ],
  },
  {
    id: "decision_planning", name: "方案生成与推荐", agent: "decision_planning_agent",
    description: "生成候选方案，并结合目标趋势、资源与证据进行推荐。",
    algorithms: [
      ["模板候选方案", "按任务模板快速生成候选方案。"],
      ["Logistic 方案推荐", "对多因子方案特征计算推荐分。"],
      ["LSTM 趋势评分", "利用目标趋势调整方案排序。"],
      ["加权规划基线", "综合风险、资源和任务收益排序方案。"],
      ["ONNX 规划模型", "调用 ONNX 模型，失败时回退加权规划。"],
    ],
  },
  {
    id: "compliance_authorization", name: "合规与授权审查", agent: "compliance_authorization_agent",
    description: "校验规则表和授权范围，并对候选方案给出风险结论。",
    algorithms: [
      ["规则表审查", "按确定性规则检查方案约束。"],
      ["Logistic 风险校准", "校准合规风险并给出授权建议。"],
      ["授权范围一致性", "联合规则与授权范围完成审查。"],
      ["ONNX 合规模型", "调用 ONNX 模型，失败时回退规则审查。"],
    ],
  },
  {
    id: "execution_control", name: "模拟执行控制", agent: "simulation_execution_agent",
    description: "在仿真环境中下发控制命令并记录资源消耗与执行进度。",
    simulationOnly: true,
    algorithms: [["Simulation Executor", "执行仿真命令并汇总进度和资源消耗。"]],
  },
  {
    id: "effect_evaluation", name: "效果评估与闭环", agent: "closed_loop_agent",
    description: "评估毁伤、态势和任务完成度，并滚动调整后续控制策略。",
    algorithms: [
      ["ResNet18 + Logistic", "基于 ROI 表征判断毁伤状态。"],
      ["ResNet18 差异特征", "提取前后图像及差异的 1536 维特征。"],
      ["K-Means 态势聚类", "聚类任务状态并形成态势分组。"],
      ["随机森林任务评估", "回归预测任务完成度。"],
      ["约束滚动时域控制", "根据威胁、不确定性和完成度滚动决策。"],
    ],
  },
];
