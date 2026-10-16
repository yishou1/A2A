# M03、M05、M06、M14、M18 在项目中的对应关系与算法设计说明

本文档用于把图片表格中的 5 个算法编号对应到当前 `jzz/integrated` 项目实现中，并说明它们在 A2A 链路中的位置、输入输出、算法原理、项目设计方式和可讲解重点。

只覆盖图片中指定的 5 项：

| 编号 | 图片中的算法类别 | 图片中的主要实现 | 项目中的对应模块 | 在项目中的主要作用 |
| --- | --- | --- | --- | --- |
| M03 | 线性回归 | `trajectory_linear_predictor` | `commander/services/a2a_algorithms_common/motion_prediction.py`、`commander/services/trajectory_linear_predictor/` | 根据前置轨迹点预测目标未来位置，为执行控制提供提前量和瞄准点 |
| M05 | 随机森林 | `threat_priority_random_forest` 及 ONNX 版 | `commander/services/a2a_algorithms_common/threat_priority_random_forest.py`、`commander/examples/threat_priority_random_forest_onnx/` | 根据目标威胁、距离、速度、价值、情报置信度等特征判断威胁优先级 |
| M06 | 传统神经网络 | `supcon_meta_classifier` 及 ONNX 版 | `commander/agent/skills/cognition/supcon_meta_classifier.py`、`commander/agent/inference/models/supcon_meta.py`、`commander/examples/supcon_meta_classifier_onnx/` | 对融合后的目标特征做类别/属性识别，如友方、中立、敌方、未知 |
| M14 | 可解释 AI | `edl_evidential_verifier` 及 ONNX 版 | `commander/agent/skills/perception/edl.py`、`commander/agent/inference/edl.py`、`commander/examples/edl_evidential_verifier_onnx/` | 对检测结果进行可信性校验，同时输出不确定性，决定通过、拒绝或人工复核 |
| M18 | 差分/变化检测 | `siamese_mask2former_damage`、`xbd_damage_assessor` | `commander/agent/skills/perception/siamese_mask2former_damage.py`、`commander/services/a2a_algorithms_common/xbd_damage_classifier.py`、`commander/services/xbd_damage_assessor/` | 对前后时相图像或 xBD 特征进行变化/毁伤判断，输出毁伤概率和毁伤结论 |

---

## 一、总体链路关系

这 5 个算法不是孤立存在的，它们分别落在项目中的感知、认知、威胁评估、执行控制和闭环评估环节。

可以按下面这条链路理解：

```text
前置感知结果
  -> M14 EDL 可解释校验
  -> M06 SupCon 目标属性/类别识别
  -> M18 变化/毁伤检测
  -> M05 随机森林威胁优先级判断
  -> M03 轨迹线性预测
  -> 执行控制与闭环评估
```

更直白地说：

- M14 先判断检测结果靠不靠谱。
- M06 再判断目标大概是什么类别或属性。
- M18 判断目标区域有没有发生变化或毁伤。
- M05 根据目标状态综合评估哪个目标更危险、更优先。
- M03 根据轨迹点预测目标之后会到哪里，给执行控制提供时间和位置依据。

---

## 二、M03 线性回归：trajectory_linear_predictor

### 1. 项目对应位置

主要代码位置：

- `commander/services/a2a_algorithms_common/motion_prediction.py`
- `commander/services/a2a_algorithms_common/service_predictors.py`
- `commander/services/trajectory_linear_predictor/app/main.py`
- `commander/models/trajectory_linear_predictor.metadata.json`

核心实现名称：

- `trajectory_linear_predictor`
- `predict_single_track`
- `fit_linear`

### 2. 在项目中做什么

M03 负责做目标轨迹预测。

它接收前置 Agent 或数据融合模块给出的目标历史轨迹点，例如某个目标在多个时间点的位置：

```json
{
  "track_id": "T-001",
  "history": [
    {"t": 0.0, "x": 100.0, "y": 200.0},
    {"t": 1.0, "x": 112.0, "y": 204.0},
    {"t": 2.0, "x": 124.0, "y": 208.0}
  ],
  "weapon_prep_sec": 2.0,
  "flight_time_sec": 5.0
}
```

然后输出目标速度、未来目标点、建议执行时刻：

```json
{
  "track_id": "T-001",
  "velocity": {"vx": 12.0, "vy": 4.0},
  "execute_at": 4.0,
  "future_t": 9.0,
  "aim_point": {"x": 208.0, "y": 236.0},
  "fit": {"r2_x": 1.0, "r2_y": 1.0}
}
```

### 3. 输入从哪里来

正常链路中，M03 不直接依赖用户手填默认值，而是使用前置目标跟踪/数据融合后的轨迹信息。

在项目设计里，它通常来自：

- `data_fusion.track_history`
- 目标跟踪 Agent 形成的 `track.history`
- 执行控制环节传入的目标历史点

也就是说，它需要的不是单点坐标，而是一串时间连续的位置点。至少需要 2 个点，否则无法拟合速度。

### 4. 算法原理

M03 使用的是普通最小二乘线性回归。

项目中把二维运动拆成两个一维问题：

```text
x = a_x * t + b_x
y = a_y * t + b_y
```

其中：

- `t` 是时间。
- `x`、`y` 是目标位置。
- `a_x` 是 x 方向速度，也就是 `vx`。
- `a_y` 是 y 方向速度，也就是 `vy`。
- `b_x`、`b_y` 是截距，表示按当前趋势反推到 t=0 时的位置。

项目中的 `fit_linear` 会计算：

```text
slope = sum((t - mean_t) * (value - mean_value)) / sum((t - mean_t)^2)
intercept = mean_value - slope * mean_t
```

这就是标准线性回归的一元最小二乘解。

### 5. 项目中的算法设计

项目没有训练一个固定模型，而是每次收到一条轨迹时，临时对这条轨迹做拟合。

这样设计的原因是：

- 轨迹预测强依赖当前目标自己的历史运动。
- 每个目标的速度、方向都不同，不适合只用一个固定参数。
- 对短时间匀速或近似匀速目标，线性模型简单、稳定、可解释。

执行控制中真正需要的是两个时间：

```text
execute_at = last_t + weapon_prep_sec
future_t = last_t + weapon_prep_sec + flight_time_sec
```

含义是：

- `last_t`：最后一次观测目标的时间。
- `weapon_prep_sec`：执行动作准备时间。
- `flight_time_sec`：动作发出后到达目标区域的时间。
- `execute_at`：什么时候开始执行。
- `future_t`：真正作用到目标时，目标可能已经移动到哪个时间点的位置。

然后 M03 用线性回归模型预测 `future_t` 时刻的目标位置，得到 `aim_point`。

### 6. 可讲解重点

汇报时可以这样讲：

> M03 在项目中用于解决“现在看到目标，不代表执行动作到达时目标还在原地”的问题。它使用目标历史轨迹点拟合 x、y 两个方向的线性运动方程，再结合准备时间和飞行时间预测未来瞄准点。算法简单，但优点是稳定、可解释、对执行控制友好。

### 7. 评估情况

项目元数据中记录了基于确定性合成轨迹的测试：

- 测试样本：160 条
- 匀速样本：120 条
- 轻微加速样本：40 条
- 位置 MAE：约 0.52
- 位置 RMSE：约 0.96

需要注意：它对匀速目标效果更好，对明显机动目标会有误差，因此更适合短时预测。

---

## 三、M05 随机森林：threat_priority_random_forest

### 1. 项目对应位置

主要代码位置：

- `commander/services/a2a_algorithms_common/threat_priority_random_forest.py`
- `commander/services/threat_priority_random_forest/app/main.py`
- `commander/models/threat_priority_random_forest.metadata.json`
- `commander/examples/threat_priority_random_forest_onnx/1.0.0/`

核心实现名称：

- `threat_priority_random_forest`
- `predict_threat_priorities`

### 2. 在项目中做什么

M05 负责判断目标威胁优先级。

它输入一批目标，每个目标带有若干数值特征：

```json
{
  "targets": [
    {
      "target_id": "T-001",
      "threat_score": 0.86,
      "distance_km": 12.5,
      "speed_mps": 280.0,
      "asset_value": 0.75,
      "intel_confidence": 0.82
    }
  ]
}
```

输出每个目标的优先级：

```json
{
  "priorities": [
    {
      "target_id": "T-001",
      "priority": "high",
      "confidence": 0.91,
      "class_probabilities": {
        "low": 0.02,
        "medium": 0.07,
        "high": 0.91
      }
    }
  ]
}
```

### 3. 输入从哪里来

M05 的输入来自前置 Agent 汇总后的目标特征，而不是自己从原始图像直接判断。

各字段含义如下：

| 输入字段 | 含义 | 常见来源 |
| --- | --- | --- |
| `target_id` | 目标编号 | 目标检测、跟踪或数据融合模块 |
| `threat_score` | 目标本身威胁程度 | 威胁评估、规则匹配、目标类型识别结果 |
| `distance_km` | 目标距离 | 传感器、定位、轨迹融合结果 |
| `speed_mps` | 目标速度 | 轨迹跟踪或 M03/M19 一类运动估计结果 |
| `asset_value` | 目标价值或保护对象价值关联 | 任务规划、资源/资产上下文 |
| `intel_confidence` | 情报置信度 | M14 校验结果、检测置信度、融合置信度 |

在 zh 分支相关闭环评估里，M05 的输出还会进一步被转换成“当前威胁压力”指标。例如多个目标都被判为 `high`，或者 `high` 的概率很高，就说明当前威胁压力更大。

### 4. 算法原理

随机森林是由很多棵决策树组成的集成模型。

一棵决策树会做类似这样的判断：

```text
如果 threat_score > 0.75 且 distance_km < 20，则倾向 high
否则如果 threat_score 中等且距离较远，则倾向 medium
否则倾向 low
```

但单棵树容易受局部规则影响。随机森林会训练很多棵树，每棵树看到的数据和特征组合略有不同，最后通过投票或概率平均得到结果。

项目中输出的是三类概率：

```text
P(low), P(medium), P(high)
```

最终选择概率最大的类别作为优先级：

```text
priority = argmax(P(low), P(medium), P(high))
confidence = max(P(low), P(medium), P(high))
```

### 5. 项目中的算法设计

项目中的随机森林模型使用 5 个特征：

| 特征 | 取值范围 | 对优先级的影响 |
| --- | --- | --- |
| `threat_score` | 0 到 1 | 越高通常越危险，是最核心特征 |
| `distance_km` | 0 到 10000 | 距离越近通常越需要优先处理 |
| `speed_mps` | 0 到 5000 | 速度越快，窗口期越短，优先级可能越高 |
| `asset_value` | 0 到 1 | 涉及高价值资产时优先级更高 |
| `intel_confidence` | 0 到 1 | 情报越可靠，模型越敢给出明确判断 |

模型训练配置记录在元数据中：

- 模型类型：`sklearn_random_forest_classifier`
- 决策树数量：160 棵
- 最大深度：8
- 叶子最小样本数：2
- 类别：`low`、`medium`、`high`
- 训练数据：360 条确定性参考场景数据

项目还提供了 ONNX 版本，位置在：

```text
commander/examples/threat_priority_random_forest_onnx/1.0.0/
```

ONNX 版的作用是方便跨语言、跨环境部署。也就是说，即使部署环境不用 Python sklearn，也可以通过 ONNX Runtime 调用同一个模型。

### 6. 为什么随机森林可以用于威胁优先级判断

威胁优先级不是只看一个指标，而是多个因素一起决定。

例如：

- 一个目标威胁值高，但距离很远，可能是 `medium`。
- 一个目标威胁值中等，但距离很近、速度很快，也可能是 `high`。
- 一个目标情报置信度低，即使威胁看起来高，也可能需要更保守判断。

随机森林适合这种场景，因为它可以学习多个特征之间的非线性组合关系，而不是写死一条简单公式。

### 7. 可讲解重点

汇报时可以这样讲：

> M05 在项目中负责把目标的威胁、距离、速度、价值、情报可信度综合起来，输出 low、medium、high 三档威胁优先级。它不是单一阈值规则，而是通过随机森林学习多特征组合关系。每棵树给出一个判断，最终通过集成投票形成稳定结果，同时输出类别概率作为置信度。

### 8. 评估情况

项目元数据记录的测试结果：

- 测试集准确率：约 0.889
- 宏平均 F1：约 0.873
- 重要性最高的特征：`threat_score`
- 其次为：`distance_km`、`asset_value`

这说明模型主要根据目标威胁程度判断，同时考虑距离和资产价值。

---

## 四、M06 传统神经网络：supcon_meta_classifier

### 1. 项目对应位置

主要代码位置：

- `commander/agent/skills/cognition/supcon_meta_classifier.py`
- `commander/agent/inference/classify.py`
- `commander/agent/inference/registry.py`
- `commander/agent/inference/models/supcon_meta.py`
- `commander/examples/supcon_meta_classifier_onnx/1.0.0/`
- `commander/services/a2a_algorithms_common/tia_predictors.py`

核心实现名称：

- `supcon_meta_classifier`
- `SupConMetaClassifier`
- `SupConMetaNet`

### 2. 在项目中做什么

M06 用于对融合后的目标特征做分类。

它不是直接看原始图片，而是接收前置多模态融合模块输出的目标嵌入向量，也就是 `fused_embeddings`。

输入示例：

```json
{
  "fused_embeddings": {
    "T-001": [0.12, -0.03, 0.44, "..."]
  },
  "support_shots": []
}
```

输出示例：

```json
{
  "classifications": [
    {
      "target_id": "T-001",
      "label": "hostile",
      "confidence": 0.96,
      "probabilities": {
        "friendly": 0.01,
        "neutral": 0.02,
        "hostile": 0.96,
        "unknown": 0.01
      }
    }
  ]
}
```

项目中的类别包括：

- `friendly`：友方
- `neutral`：中立
- `hostile`：敌方
- `unknown`：未知

### 3. 输入从哪里来

M06 的主要输入来自前置多模态融合 Agent。

简单说，前面可能已经把图像、文本、雷达、传感器或其他特征融合成一个向量：

```text
原始多源数据 -> 多模态融合 -> fused_embeddings -> M06 分类
```

M06 不关心这个向量最开始来自哪一种传感器，它只关心融合后的数值表示。这个设计可以让分类模块和原始数据类型解耦。

### 4. 算法原理

M06 名字里的 SupCon 指的是 Supervised Contrastive Learning，中文可以理解为有监督对比学习。

它的核心思想是：

```text
同一类样本在向量空间里拉近
不同类样本在向量空间里推远
```

例如：

- 友方目标的向量应该互相接近。
- 敌方目标的向量应该互相接近。
- 友方和敌方之间应该尽量远。

训练完成后，模型会形成每个类别的代表性区域或类别原型。新目标来了以后，模型比较这个目标向量和各类别原型的距离或相似度，然后输出分类概率。

项目中还使用了温度系数 `temperature = 0.07`。它的作用是控制概率分布的尖锐程度：

- 如果某一类明显最接近，输出概率会更集中。
- 如果多个类别都差不多，概率会更分散，置信度更低。

### 5. 项目中的算法设计

项目中 M06 的调用链大致是：

```text
tia_predictors.predict_supcon_meta_classifier
  -> agent.skills.cognition.supcon_meta_classifier.SupConMetaClassifier
  -> agent.inference.classify.classify_targets
  -> registry.get_supcon_meta
  -> SupConMetaNet.classify
```

它支持两种运行方式：

1. 真实模型模式：加载 `supcon_meta_s.safetensors` 检查点进行推理。
2. Mock 模式：在没有真实模型时使用确定性逻辑返回结果，主要用于开发链路验证。

ONNX 版本位于：

```text
commander/examples/supcon_meta_classifier_onnx/1.0.0/
```

ONNX 元数据中记录：

- 模型类型：`onnx_supervised_contrastive_prototypical_mlp`
- 输入维度：256
- 类别顺序：`friendly`、`neutral`、`hostile`、`unknown`
- 参数量：99,200
- 温度系数：0.07

### 6. 为什么这里属于传统神经网络

它使用的是 MLP 类神经网络和向量分类结构，不是大语言模型，也不是规则系统。

它的判断依据是数值特征在向量空间中的分布关系：

```text
目标向量 -> 神经网络编码/投影 -> 与类别原型比较 -> softmax 概率 -> 类别结果
```

所以它适合处理前面融合出来的高维特征，而不是人工写一堆规则。

### 7. 可讲解重点

汇报时可以这样讲：

> M06 在项目中用于目标属性分类。它接收多模态融合后的目标嵌入向量，通过有监督对比学习把同类目标拉近、异类目标拉远，再根据目标向量与类别原型的相似度输出 friendly、neutral、hostile、unknown 四类概率。它的优点是能利用融合特征进行稳定分类，并且可以通过 ONNX 版本部署。

### 8. 评估情况

ONNX 元数据中记录的参考测试结果为：

- 准确率：1.0
- 宏平均 F1：1.0
- 平均置信度：约 0.999954

需要注意：元数据也说明该结果基于合成聚类数据，不等同于真实复杂场景的最终效果。因此在真实应用中仍需要接入真实样本验证。

---

## 五、M14 可解释 AI：edl_evidential_verifier

### 1. 项目对应位置

主要代码位置：

- `commander/agent/skills/perception/edl.py`
- `commander/agent/inference/edl.py`
- `commander/agent/inference/registry.py`
- `commander/agent/inference/models/edl_head.py`
- `commander/examples/edl_evidential_verifier/1.0.0/`
- `commander/examples/edl_evidential_verifier_onnx/1.0.0/`
- `commander/services/a2a_algorithms_common/tia_predictors.py`

核心实现名称：

- `edl_evidential_verifier`
- `EvidentialVerifier`
- `EvidentialHead`

### 2. 在项目中做什么

M14 用于验证前置检测结果是否可信。

目标检测模型可能会输出很多候选框，但不是每个检测都可靠。M14 的作用是对每个 detection 做二次判断：

- 可信：`verified`
- 不可信：`rejected`
- 不确定，需要人工复核：`manual_review`

输入示例：

```json
{
  "detections": [
    {
      "detection_id": "D-001",
      "confidence": 0.82,
      "bbox": [0.42, 0.31, 0.18, 0.12],
      "damage_score": 0.64
    }
  ]
}
```

输出示例：

```json
{
  "assessments": [
    {
      "detection_id": "D-001",
      "decision": "verified",
      "verified_probability": 0.88,
      "epistemic_uncertainty": 0.19,
      "aleatoric_uncertainty": 0.08,
      "review_required": false
    }
  ],
  "verified_detections": ["D-001"],
  "rejected_detections": [],
  "review_queue": [],
  "summary": {
    "total": 1,
    "verified": 1,
    "rejected": 0,
    "manual_review": 0
  }
}
```

### 3. 输入从哪里来

M14 的输入来自前置目标检测或感知 Agent。

它使用的关键特征包括：

| 特征 | 含义 | 来源 |
| --- | --- | --- |
| `detector_confidence` | 检测器原始置信度 | 前置目标检测 Agent |
| `bbox_width_normalized` | 目标框宽度归一化值 | 检测框 |
| `bbox_height_normalized` | 目标框高度归一化值 | 检测框 |
| `bbox_center_x_normalized` | 目标框中心 x 坐标 | 检测框 |
| `bbox_center_y_normalized` | 目标框中心 y 坐标 | 检测框 |
| `damage_score` | 初步毁伤或异常分数 | 检测/变化检测/毁伤评估结果 |

也就是说，M14 不是凭空判断，而是基于检测框、检测置信度和毁伤相关分数判断这个 detection 是否应该进入后续链路。

### 4. 算法原理

M14 使用 Evidential Deep Learning，简称 EDL，中文可以叫“证据深度学习”。

普通分类模型通常只输出：

```text
这个检测是可信的概率 = 0.88
```

但 EDL 额外关心一个问题：

```text
模型为什么这么判断？它到底有多少证据支持这个判断？
```

EDL 的核心设计是：

```text
输入特征 -> 神经网络 -> 每个类别的 evidence -> Dirichlet 分布 -> 类别概率 + 不确定性
```

其中：

- `evidence` 表示模型为某个类别收集到的证据强度。
- `alpha` 是 Dirichlet 分布参数，通常由 `evidence + 1` 得到。
- `probability` 是根据 Dirichlet 参数归一化得到的类别概率。
- `epistemic_uncertainty` 表示模型知识不足导致的不确定性。
- `aleatoric_uncertainty` 表示数据本身噪声导致的不确定性。

直白地说：

- 如果模型证据很多，而且大多支持 `verified`，就通过。
- 如果模型证据很多，而且支持 `rejected`，就拒绝。
- 如果模型证据不足，哪怕概率看起来还可以，也会进入人工复核。

### 5. 项目中的判定规则

ONNX 元数据中记录的策略包括：

- `verified_probability_threshold = 0.5`
- `maximum_epistemic_uncertainty = 0.45`
- `manual_review_margin = 0.08`

可以理解为：

```text
如果 verified 概率 >= 0.5，并且 epistemic_uncertainty 不高，则 verified
如果 verified 概率明显低，则 rejected
如果概率接近边界，或者 epistemic_uncertainty 太高，则 manual_review
```

项目这么设计的原因是：检测结果会影响后续跟踪、威胁评估和执行控制，不能只看检测器原始置信度。一个检测框即使置信度高，如果模型认为证据不足，也不应该盲目进入后续链路。

### 6. 可解释性体现在哪里

M14 的可解释性主要体现在它不仅给结果，还给判断依据：

| 输出 | 解释作用 |
| --- | --- |
| `verified_probability` | 可信类别概率 |
| `class_probabilities` | 可信/拒绝两个类别的概率分布 |
| `evidence` | 模型对每类判断的证据强度 |
| `alpha` / `strength` | Dirichlet 证据分布参数 |
| `epistemic_uncertainty` | 模型不了解、不确定的程度 |
| `aleatoric_uncertainty` | 输入数据噪声造成的不确定性 |
| `review_required` | 是否需要人工复核 |

所以它不是一个黑盒式“通过/不通过”，而是能说明为什么通过、为什么拒绝、为什么需要复核。

### 7. 可讲解重点

汇报时可以这样讲：

> M14 在项目中是检测结果进入后续链路前的可信性闸门。它使用证据深度学习，不只输出 verified 或 rejected，还输出 evidence、Dirichlet 概率和 epistemic uncertainty。这样后续模块可以知道一个检测结果是“真的可信”，还是“模型其实没有足够证据，只是概率碰巧高”。

### 8. 评估情况

ONNX 元数据中记录：

- 准确率：约 0.903
- 宏平均 F1：约 0.901
- Brier Score：约 0.0667
- ECE10：约 0.0358
- 80% 覆盖率下选择性准确率：约 0.939

这类指标除了看分类对不对，还看概率校准和不确定性是否可靠。

---

## 六、M18 差分/变化检测：siamese_mask2former_damage 与 xbd_damage_assessor

### 1. 项目对应位置

主要代码位置：

- `commander/agent/skills/perception/siamese_mask2former_damage.py`
- `commander/agent/inference/damage.py`
- `commander/agent/inference/models/siamese_mask2former.py`
- `commander/services/a2a_algorithms_common/xbd_damage_classifier.py`
- `commander/services/xbd_damage_assessor/app/main.py`
- `commander/models/xbd_damage_classifier.metadata.json`

核心实现名称：

- `siamese_mask2former_damage`
- `SiameseMask2FormerDamage`
- `xbd_damage_assessor`
- `assess_damage`

### 2. 在项目中做什么

M18 用于判断目标区域是否发生变化或毁伤。

项目中对应两条实现路线：

1. `siamese_mask2former_damage`：更偏图像变化检测，比较前后两张图。
2. `xbd_damage_assessor`：更偏 xBD 毁伤评估，使用手工特征和 CNN 特征输出毁伤概率。

它们解决的是同一个核心问题：

```text
同一个区域在行动前和行动后相比，到底有没有明显变化或毁伤？
```

### 3. 输入从哪里来

M18 的输入通常来自图像感知、卫星图像、无人机图像或前置检测 Agent。

#### 3.1 Siamese Mask2Former 路线输入

输入示例：

```json
{
  "reference_frame": {
    "sensor_id": "EO-001",
    "image": "base64_or_image_ref_before"
  },
  "frames": [
    {
      "sensor_id": "EO-001",
      "modality": "eo_ir",
      "image": "base64_or_image_ref_after"
    }
  ]
}
```

输出示例：

```json
{
  "damage_reports": [
    {
      "sensor_id": "EO-001",
      "damage_score": 0.74,
      "change_ratio": 0.19,
      "damage_mask_ref": "mask_ref_or_path"
    }
  ]
}
```

含义：

- `reference_frame` 是行动前或基准图像。
- `frames` 是行动后或当前图像。
- `damage_score` 表示毁伤程度评分。
- `change_ratio` 表示变化区域占比。
- `damage_mask_ref` 表示变化/毁伤掩膜结果引用。

#### 3.2 xBD 路线输入

xBD 评估支持两类输入。

第一类是特征输入：

```json
{
  "input_mode": "features",
  "handcrafted_features": [0.12, 0.04, 0.33, "..."],
  "cnn_embedding": [0.01, -0.08, 0.21, "..."]
}
```

第二类是图像输入：

```json
{
  "input_mode": "images",
  "pre_image": "before_image_ref_or_base64",
  "post_image": "after_image_ref_or_base64",
  "polygon": [[100, 120], [180, 120], [180, 210], [100, 210]]
}
```

输出示例：

```json
{
  "damage_probability": 0.81,
  "damage_label": 1,
  "damage_result": "damaged",
  "decision_threshold": 0.645,
  "model_source": "xbd_handcrafted_plus_resnet18_lr",
  "feature_version": "xbd_damage_v1",
  "assessment_status": "model_estimate"
}
```

### 4. 算法原理：差分/变化检测

变化检测的基本思想很直观：

```text
同一区域的前图像 + 后图像 -> 对比差异 -> 判断变化区域 -> 判断是否毁伤
```

但项目里不是简单做像素相减，因为真实图像会有光照、角度、传感器噪声、配准误差等问题。

所以 M18 使用两种更稳的设计。

### 5. Siamese Mask2Former 的设计

Siamese 的意思是“双塔”或“孪生网络”。

它把前后两张图分别送入结构相同的特征提取分支：

```text
行动前图像 -> 特征提取分支 A -> before feature
行动后图像 -> 特征提取分支 B -> after feature
before feature + after feature -> 差异建模 -> 分割/变化掩膜 -> damage_score
```

Mask2Former 类结构适合做分割任务，也就是不仅判断“有没有变化”，还可以定位“哪里发生变化”。

项目中的输出 `damage_mask_ref` 就是变化/毁伤区域掩膜的引用。

这条路线适合讲成：

> 它不是只给一个分数，而是通过前后图像对比，把变化区域以 mask 的形式找出来，再根据变化面积、变化强度等形成毁伤评分。

### 6. xBD damage assessor 的设计

xBD 路线更像是“特征工程 + 分类器”的毁伤判断。

项目元数据记录的模型来源是：

```text
xbd_handcrafted_plus_resnet18_lr
```

可以拆成三部分理解：

1. `xbd`：使用 xBD 灾损/建筑毁伤评估思路。
2. `handcrafted`：提取人工设计的变化特征。
3. `resnet18_lr`：使用 ResNet18 图像嵌入，再接逻辑回归类判别器。

项目中的特征维度：

- 手工特征：31 维
- CNN 特征：1536 维
- 总输入维度：1567 维

手工特征可以理解为：

- 前后图像亮度差异
- 纹理变化
- 区域形状变化
- 多边形 ROI 内部统计特征
- 局部异常强度

CNN 特征可以理解为：

- 用 ResNet18 从前后图像区域中提取更高层的视觉表示。
- 它能捕捉人工特征不容易描述的复杂变化。

最后分类器输出一个毁伤概率：

```text
damage_probability = model(feature_vector)
```

项目中的判定阈值是：

```text
decision_threshold = 0.645
```

因此：

```text
如果 damage_probability >= 0.645，则 damage_result = damaged
如果 damage_probability < 0.645，则 damage_result = no_damage
```

### 7. M18 与闭环评估的关系

在 zh 分支涉及的闭环评估里，M18 的结果可以转成“毁伤效果”指标。

例如：

```json
{
  "damage_probability": 0.81,
  "damage_result": "damaged"
}
```

可以理解为：

- `damage_probability` 越高，表示毁伤证据越强。
- `damage_result = damaged` 表示达到模型阈值，被判定为已毁伤。
- 闭环评估可以用它判断任务是否达到效果，是否需要补充动作或重新规划。

### 8. 可讲解重点

汇报时可以这样讲：

> M18 在项目中负责变化检测和毁伤确认。它通过行动前后的同区域图像对比，判断目标是否发生明显变化。Siamese Mask2Former 路线更强调变化区域分割和 mask 输出，xBD assessor 路线则使用 31 维手工变化特征加 1536 维 CNN 特征，最终输出毁伤概率。项目中使用 0.645 作为判定阈值，超过阈值则认为目标区域已毁伤。

### 9. 评估情况

xBD 模型元数据记录：

- 测试集数量：31,958
- 准确率：约 0.794
- F1：约 0.619
- Balanced Accuracy：约 0.742
- Precision：约 0.607
- Recall：约 0.631
- 判定阈值：0.645

需要注意：当前项目中 xBD assessor 的模型与元数据更完整；`siamese_mask2former_damage` 在链路上有实现入口，但更偏工程接口和变化检测能力封装，完整模型制品和实测元数据不如 xBD assessor 完整。

---

## 七、五个算法之间如何组合成项目能力

这 5 个模块共同支撑的是一个更完整的“感知-理解-评估-控制-反馈”过程。

| 环节 | 使用的算法 | 解决的问题 |
| --- | --- | --- |
| 检测可信性过滤 | M14 EDL | 检测结果是否可靠，是否需要人工复核 |
| 目标属性理解 | M06 SupCon | 目标是友方、中立、敌方还是未知 |
| 毁伤/变化确认 | M18 变化检测 | 前后图像是否显示目标被毁伤或发生变化 |
| 威胁排序 | M05 随机森林 | 哪些目标更危险、更优先处理 |
| 执行预测 | M03 线性回归 | 目标未来会到哪里，什么时候执行更合适 |

从输入数据流看：

```text
检测框、置信度、图像、目标轨迹、多模态特征
  -> M14 过滤不可靠检测
  -> M06 识别目标属性
  -> M18 判断变化/毁伤
  -> M05 计算威胁优先级
  -> M03 预测未来位置
  -> 执行控制与闭环优化
```

从闭环评估角度看：

- M14 提供“情报是否可靠”的依据。
- M18 提供“毁伤效果”的依据。
- M05 提供“当前威胁压力”的依据。
- M03 提供“控制是否及时、执行位置是否合理”的依据。
- M06 提供“目标类别/属性是否明确”的依据。

---

## 八、适合汇报的一页总结

可以用下面这段作为汇报口径：

> 项目中对应图片的 M03、M05、M06、M14、M18 分别承担轨迹预测、威胁排序、目标属性分类、检测可信性校验和毁伤变化评估。M03 使用线性回归，根据历史轨迹预测目标未来位置；M05 使用随机森林，把威胁值、距离、速度、资产价值、情报置信度组合成 low/medium/high 威胁优先级；M06 使用有监督对比学习神经网络，对融合特征进行 friendly/neutral/hostile/unknown 分类；M14 使用证据深度学习，在输出检测可信性的同时给出不确定性和人工复核依据；M18 使用前后图像差分思路，其中 Siamese Mask2Former 负责变化区域分割，xBD assessor 使用手工特征加 CNN 特征输出毁伤概率。五者共同构成从感知结果过滤、目标理解、威胁评估到执行控制和闭环反馈的关键算法链路。

