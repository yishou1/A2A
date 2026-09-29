# xBD 毁伤评估模型迭代记录

本文档记录 A2A 闭环优化模块（`closed_loop_agent/closed_loop_core.py`）在 xBD 训练数据上的毁伤评估模型迭代过程，涵盖数据规模、训练与测试方法、各阶段代码改动、修改动机及量化结果。相关算法背景见 [`closed_loop_algorithms.md`](closed_loop_algorithms.md)。

---

## 概述

迭代目标是在 xBD 灾前/灾后影像与建筑多边形标注上，构建可嵌入多智能体闭环流程的毁伤评估模型，并尽可能满足协议中的算法约束与准确率、时延指标。

项目早期在**不引入 sklearn / PyTorch** 的前提下，以纯 Python 实现逻辑回归、随机森林与 K-Means；后期为对齐协议「逻辑回归分类」要求并冲击 **92% 二分类准确率**，引入 **ResNet18 深度特征 + sklearn 逻辑回归** 路线，毁伤分类器回归逻辑回归，深度网络仅承担特征抽取。

迭代路径可概括为：

```
基线逻辑回归（73.6%）
  → 特征工程增强（v2 特征）
  → 分层划分 + 随机森林（~80%）
  → 灾害类型嵌入 + 自适应决策策略（~82%）
  → 三分类评估体系（78.3%，阈值 75%，阶段性达标）
  → ResNet18 嵌入 + 逻辑回归（82.8%，恢复 92% 二分类口径，仍未达标）
```

**评估口径说明**：中间曾将最终验收调整为**三分类准确率 ≥ 75%**；在明确协议要求「逻辑回归分类 + 毁伤准确率 ≥ 92%」后，当前主路径恢复为**二分类 holdout**，毁伤分类器固定为**逻辑回归**（`SklearnLogisticDamageModel`），K-Means 与随机森林回归保持不变。

---

## 数据与标签体系

### 原始数据

| 项目 | 说明 |
|------|------|
| 训练影像 | `data/xbd/train/train/images/`，2799 对灾前/灾后 PNG |
| 训练标注 | `data/xbd/train/train/labels/`，建筑多边形 + `subtype` 字段 |
| 测试影像 | `data/xbd/test/test/images/`，无公开标签，不参与 holdout 评估 |
| 特征表输出 | `data/xbd/processed/xbd_damage_features_train.csv` |

### 有效样本规模

特征抽取脚本 `scripts/extract_xbd_damage_features.py` 对每个建筑多边形生成一行特征。迭代过程中样本数变化如下：

| 阶段 | 建筑样本数 | 说明 |
|------|-----------|------|
| 初版全量抽取 | 162,787 | 含 `un-classified` 标签 |
| v2 特征重抽（最终） | **159,794** | 排除 `un-classified`，跳过 2,993 条 |

### 标签分布（159,794 条，v2 特征表）

**二分类**（`damage_label`：`no-damage` → 0，其余 → 1）：

| 类别 | 数量 | 占比 |
|------|------|------|
| 未毁伤 (0) | 117,426 | 73.5% |
| 毁伤 (1) | 42,368 | 26.5% |

**四分类原始 subtype**：

| subtype | 数量 |
|---------|------|
| no-damage | 117,426 |
| minor-damage | 14,980 |
| major-damage | 14,161 |
| destroyed | 13,227 |

**三分类**（最终评估，`damage_tier`）：

| tier | 名称 | 映射规则 | 数量 | 占比 |
|------|------|----------|------|------|
| 0 | no_damage | `no-damage` | 117,426 | 73.5% |
| 1 | minor | `minor-damage` | 14,980 | 9.4% |
| 2 | severe | `major-damage` + `destroyed` | 27,388 | 17.1% |

类别极度不平衡是各阶段准确率接近多数类基线（约 74%）的主要原因之一。

---

## 训练与测试方法

### 特征抽取（手工特征）

```bash
cd A2A
python scripts/extract_xbd_damage_features.py \
  --input-root data/xbd/train/train \
  --output-csv data/xbd/processed/xbd_damage_features_train.csv \
  --report-json data/xbd/processed/xbd_damage_features_train_report.json
```

脚本对每栋建筑裁剪灾前/灾后 ROI，在 mask 内统计光谱、纹理、亮度等像素级特征，写入 CSV。`--input-root` 须指向含 `images/` 与 `labels/` 的目录（即 `train/train`，而非 `train`）。

### 特征抽取（CNN 嵌入，迭代六）

```bash
python scripts/extract_xbd_cnn_embeddings.py \
  --input-root data/xbd/train/train \
  --output-npz data/xbd/processed/xbd_cnn_embeddings_train.npz \
  --batch-size 32 \
  --device cpu
```

| 项目 | 说明 |
|------|------|
| 骨干网络 | `torchvision.models.resnet18`（ImageNet 预训练） |
| 输入 | 建筑多边形 mask 后的 pre / post ROI，224×224 |
| 嵌入 | pre、post、归一化 diff 三路各 512 维，拼接 **1536 维** |
| 索引键 | `{sample_id}:{building_index}` |
| 输出 | `xbd_cnn_embeddings_train.npz` |
| 全量耗时 | 约 **83 分钟**（159,794 条，CPU） |

CNN 与手工 CSV **分文件存储**；训练时由 `_load_cnn_embedding_store()` 加载并按 row key 与 CSV 行合并。

### Holdout 评估（毁伤模型）

毁伤模型在 `_train_models()` 内完成训练与 holdout 测试，**不依赖** xBD 官方 test 集（无标签）。

**数据划分**（`seed = 20260412`）：

| 子集 | 比例 | 约计样本数 | 用途 |
|------|------|-----------|------|
| 测试集 | 20% | ~31,959 | holdout 准确率 |
| 训练集 | 80% | ~127,835 | 进一步划分 |
| └ 拟合集 (fit) | 训练集的 80% | ~102,268 | 模型参数估计 |
| └ 拟合集 (LR 路径，超限下采样) | 同上 | → **上限 100,000** | sklearn LR（saga） |
| └ 验证集 (val) | 训练集的 20% | ~25,567 | 阈值/策略校准 |

- **二分类**：`_stratified_split_with_ids` 按正负类分层。
- **三分类（历史）**：`_stratified_split_multiclass_with_ids` 按 tier 0/1/2 分层。
- **RF 路径**：拟合集超 60,000 时降至 60,000（控制纯 Python 训练耗时）。
- **CNN + LR 路径**：拟合集超 100,000 时降至 100,000。

**手工特征**（`_build_xbd_model_features`，31 维）：基础 12 维 + 衍生 9 维 + 灾害 one-hot 10 维。

**CNN + LR 合并特征**（`_build_damage_feature_row`）：**31 + 1536 = 1567 维** → `StandardScaler` → `PCA`（≤512 维）→ 逻辑回归。闭环推理从 NPZ 按 `target_id` / `building_index` 查 CNN 段。

### 闭环仿真测试

全量 benchmark 由 `scripts/run_full_train_test.py` 驱动：

```bash
python scripts/run_full_train_test.py \
  --feature-csv data/xbd/processed/xbd_damage_features_train.csv \
  --cnn-npz data/xbd/processed/xbd_cnn_embeddings_train.npz \
  --result-json data/xbd/processed/xbd_closed_loop_result_cnn_lr.json \
  --live-target-limit 200 \
  --cycles 3
```

| 项目 | 配置 |
|------|------|
| 闭环目标数 | 200（取自特征表前 200 行，满足协议 ≥ 50） |
| 控制周期 | 3 |
| 毁伤 holdout | 与上文相同，在 `_train_models` 内完成 |
| 任务完成度 | 无 SC2LE 真实数据时使用模拟数据，MAE/R² 仅验证 pipeline 连通性 |

`effect_assessment.damage_accuracy` 与 `requirement_report` 中的准确率均引用 holdout 测试结果，**非**闭环 200 目标的在线准确率。

### 结果文件对照

| 文件 | 对应迭代 |
|------|----------|
| `xbd_closed_loop_result.json` | 小规模 demo（15 目标） |
| `xbd_closed_loop_result_full.json` | 迭代一：基线全量 |
| `xbd_closed_loop_result_v2.json` | 迭代四：RF 二分类最优 |
| （训练脚本直调 `_train_models`） | 迭代五：三分类 RF |
| `xbd_closed_loop_result_cnn_lr.json` | **迭代六：CNN + LR（当前主路径）** |

---

## 迭代阶段：流水线连通验证

### 改动内容

使用默认参数运行闭环 demo，仅抽取少量目标（15 个），毁伤模型在无足够真实 CSV 时回退至 `_generate_xbd_like_damage_data()` 模拟数据。

### 修改动机

验证 `extract_xbd_damage_features.py` → `_train_models()` → `_closed_loop_optimization()` 链路可正常执行。

### 结果

| 指标 | 数值 |
|------|------|
| `damage_accuracy` | 0.0 |
| 闭环目标数 | 15（未达协议 ≥ 50） |
| 总耗时 | ~0.15 s |

**结论**：pipeline 可运行，但尚未接入全量 xBD 特征表，毁伤指标无参考价值。

---

## 迭代阶段：基线全量训练（逻辑回归 + v1 特征）

### 改动内容

对 2799 对影像执行全量特征抽取，以逻辑回归（`LogisticRegressionGD`）作为毁伤分类器，使用 6 维基础特征（`pre_area`、`spectral_delta`、`texture_delta`、`heat_signature`、`crater_density`、`normalized_distance` 等），随机或简单划分训练/测试。

**关键代码位置**：`closed_loop_core.py` 中 `_train_models()` 早期逻辑；特征抽取见 `extract_xbd_damage_features.py` 初版 `_polygon_features()`。

### 修改动机

建立可复现的全量 baseline，对照协议要求的 92% 二分类准确率。

### 诊断结论

| 现象 | 分析 |
|------|------|
| 准确率 73.6% | 与未毁伤占比 ~74% 接近，模型倾向预测多数类 |
| 单特征规则上限 ~72.5% | 对 v1 特征穷举阈值后仍无法突破 73%，特征区分度不足 |
| 类别不平衡 | 毁伤样本 precision/recall 均偏低 |

### 结果

| 指标 | 数值 |
|------|------|
| 特征样本数 | 162,787（含 un-classified） |
| holdout `damage_accuracy` | **73.6%** |
| F1 / 精确率 / 召回率 | 未系统输出 |
| 协议 92% 二分类 | 未达标 |
| 训练 + 闭环总耗时 | ~89 s |
| 结果文件 | `xbd_closed_loop_result_full.json` |

---

## 迭代阶段：v2 特征工程

### 改动内容

在 `extract_xbd_damage_features.py` 的 `_polygon_features()` 中新增 7 维像素级统计特征，并排除 `un-classified` 样本：

| 特征 | 含义 |
|------|------|
| `std_spectral` | mask 内光谱变化标准差 |
| `max_spectral` | 最大像素光谱差 |
| `high_change_ratio` | 光谱 > 0.15 的像素占比 |
| `severe_damage_ratio` | 光谱 > 0.20 且亮度下降 > 0.06 的像素占比 |
| `collapse_ratio` | 亮度下降 > 0.14 的像素占比 |
| `post_brightness` | 灾后平均亮度 |
| `brightness_drop` | 灾前/灾后亮度差（仅保留正值） |

**特征阈值逻辑**（`_polygon_features` 核心片段）：

```python
if spectral > 0.15:
    high_change += 1
if spectral > 0.20 and post_brightness < pre_brightness - 0.06:
    severe_damage += 1
if post_brightness < pre_brightness - 0.14:
    collapse += 1
```

同步在 `_build_xbd_model_features()` 中接入上述字段，并构造 `damage_score`、`change_peak` 等衍生特征。

### 修改动机

v1 特征仅使用 mask 内均值，无法刻画毁伤的空间异质性；`major-damage` 与 `no-damage` 的均值特征高度重叠。v2 特征引入极值、分位数代理与比例型特征，以提升可分性。

### 结果

| 指标 | 数值 |
|------|------|
| 有效样本数 | 159,794 |
| 仅换特征 + 仍用 LR / 简单划分 | **~70.9%**（短暂下降，因特征尺度变化后 LR 未同步调参） |
| 特征抽取耗时 | ~20–30 min（全量 2799 对影像） |

**说明**：v2 特征本身在子集上可带来提升，但必须配合分类器与划分策略升级才能体现收益。

---

## 迭代阶段：分层划分与随机森林

### 改动内容

**数据划分**：新增 `_stratified_split` / `_stratified_split_with_ids`，按类别比例分层抽样，替代随机切分。

**分类器**：样本数 ≥ 5000 时采用 `RandomForestClassifier`（纯 Python 实现，bagging + 决策树），替代 `LogisticRegressionGD`；小样本仍保留逻辑回归。

**类权重**：逻辑回归路径增加正负类权重，缓解不平衡。

**关键代码**（`RandomForestClassifier.fit` 结构）：

```python
for index in range(self.trees):
    sample_x, sample_y = [], []
    for _ in range(len(x_rows)):
        pos = rng.randrange(len(x_rows))
        sample_x.append(x_rows[pos])
        sample_y.append(label_rows[pos])
    tree = ClassificationTree(...).fit(sample_x, sample_y)
    self.models.append(tree)
```

### 修改动机

- 随机划分导致少数类在测试集中波动大，holdout 指标不稳定；
- 逻辑回归对非线性边界与特征交互拟合不足；
- 随机森林可天然处理特征非线性与噪声。

### 结果（中间实验，未单独落盘 JSON）

| 配置 | holdout 准确率 |
|------|---------------|
| v2 特征 + 分层 + 全局 RF | **~80.34%** |
| 单特征 `severe_damage_ratio` 规则 | ~74%（对照） |

---

## 迭代阶段：灾害嵌入与自适应决策策略

### 改动内容

**灾害类型特征**：`_disaster_bucket_features(sample_id, buckets=10)` 将灾害名（`sample_id` 前缀，如 `hurricane-harvey`）映射为 10 维 one-hot，使模型可学习灾害特异性模式。

**局部随机森林**：`_train_disaster_local_forests()` 对样本量 ≥ 2500 的灾害单独训练 RF，供后续策略选用。

**自适应策略**（`DisasterAdaptiveDamageModel` + `_calibrate_disaster_strategies()`）：在验证集上为每种灾害从候选策略中选取最优：

| 策略类型 | 说明 |
|----------|------|
| `prob` | 全局/局部 RF 概率 + 阈值 |
| `dual` | 概率阈值 + `damage_score` 双条件 |
| `feature` | 单特征阈值（如 `severe_damage_ratio`） |
| `or_rule` | 概率或特征规则取并集 |

**策略评分**（以准确率为主）：

```python
score = 0.92 * accuracy + 0.05 * balanced + 0.03 * f1
```

**曾尝试但回退的方案**：

- `HybridDamageClassifier`（全局 + 局部 LR 混合）：验证集准确率略降，已移除；
- 纯局部模型替代全局：泛化不足，保留为策略组件而非完全替换。

### 修改动机

不同灾害的影像特征分布差异显著（如 `hurricane-harvey` 与 `mexico-earthquake`），单一全局模型在难例灾害上准确率可低至 ~56%，而在易例灾害上可达 ~99%。按灾害校准决策边界可提升整体 holdout 表现。

### 结果

| 指标 | 数值 |
|------|------|
| holdout `damage_accuracy` | **81.98%**（`xbd_closed_loop_result_v2.json`） |
| F1 | ~0.64 |
| 精确率 / 召回率 | ~0.68 / ~0.60 |
| 协议 92% 二分类 | 仍未达标 |
| 训练 + 闭环总耗时 | ~149 s |

**分灾害测试准确率（holdout，实验记录）**：

| 灾害 | 准确率 | 选用策略 |
|------|--------|----------|
| mexico-earthquake | ~99.6% | 全局概率 |
| hurricane-harvey | ~55.7% | 局部特征 / 双阈值 |
| 其余灾害 | 75%–95% 不等 | 自动选择 |

**瓶颈**：`hurricane-harvey` 中 `major-damage` 与 `no-damage` 的 v2 特征均值重叠，`minor-damage` 与 `no-damage` 亦难区分；手工特征在该灾害上接近可分的上限。

---

## 迭代阶段：三分类评估体系

### 改动动机

在手工特征 + 纯 Python 模型约束下，92% 二分类准确率难以达到。经需求调整，**最终评估改为三分类**，达标阈值设为 **75%**。

### 标签映射

`_tier3_label()`（`closed_loop_core.py`）：

```python
# 0 = no-damage
# 1 = minor-damage
# 2 = major-damage / destroyed
```

`extract_xbd_damage_features.py` 同步输出 `damage_tier` 列；若 CSV 无该列，加载时由 `subtype` / `severity_label` 在线推导。

### 模型结构

| 组件 | 说明 |
|------|------|
| `MulticlassClassificationTree` | 3 类 Gini  impurity 决策树 |
| `MulticlassRandomForest` | 31 棵树，max_depth=12，输出类概率分布 |
| `Tier3EnsembleModel` | 全局 RF + 按灾害局部 RF（`_train_disaster_local_tier3_forests`） |
| 分层划分 | `_stratified_split_multiclass_with_ids` |

**预测接口**：

- `predict_classes(rows, sample_ids)` → tier ∈ {0, 1, 2}
- `predict_proba(rows, sample_ids)` → P(damaged) = P(tier=1) + P(tier=2)
- 闭环中 `damage_confirmed = (tier >= 1)`

**达标常量**：

```python
DAMAGE_TIER3_ACCURACY_REQUIREMENT = 0.75
TIER3_CLASS_NAMES = ("no_damage", "minor", "severe")
```

**requirement_report 字段**（当前版本）：

```python
"xbd_damage_tier3_accuracy_requirement": 0.75,
"meets_xbd_damage_tier3_accuracy": accuracy >= 0.75,
```

### 实现修复

三分类集成预测中，`MulticlassRandomForest.predict_distribution_one()` 曾向 `_mean()` 传入 generator 导致 `TypeError`；已改为列表推导：

```python
_mean([tree.predict_distribution_one(x_row)[class_idx] for tree in self.models])
```

### 结果

| 指标 | 数值 |
|------|------|
| holdout `damage_tier3_accuracy` | **78.27%** |
| 协议 75% 三分类 | **达标** |
| 训练耗时（仅 `_train_models`） | ~217 s |

**分类别 holdout 指标**：

| 类别 | support | recall | precision |
|------|---------|--------|-----------|
| no_damage | 23,485 | 0.942 | 0.828 |
| minor | 2,996 | 0.368 | 0.519 |
| severe | 5,477 | 0.327 | 0.575 |

**分析**：

- 主类 `no_damage` 识别充分，支撑整体准确率；
- `minor` 与 `severe` 互混淆是主要误差来源，与四分类原始标注中 `major-damage` / `minor-damage` 特征重叠一致；
- 三分类准确率（78.3%）低于二分类 RF 最优（82.0%），属任务难度提升后的预期现象。

---

## 迭代阶段：ResNet18 嵌入 + 逻辑回归（协议对齐）

### 背景与动机

协议对「效果评估与闭环优化」有两条硬性约束（见需求说明）：

| 约束类型 | 要求 |
|----------|------|
| **算法** | 毁伤评估采用 **逻辑回归分类**；态势为 K-Means；任务完成度为 **随机森林回归** |
| **指标** | xBD 毁伤准确率 **≥ 92%**；态势更新 **≤ 1 s**；同时处理目标 **≥ 50** |

此前迭代为提升准确率，毁伤主分类器已替换为随机森林（~82%）或三分类 RF（78.3%，75% 阈值达标）。这与「逻辑回归分类」不符，且 **92% 二分类** 仍未达到。

**为何采用「CNN 特征 + 逻辑回归」而非继续调 RF：**

1. **合规**：分类决策层必须是逻辑回归；深度模型仅作 **特征抽取**，不改变协议所要求的分类算法类型。
2. **特征瓶颈**：手工 31 维特征 + 规则上限约 72%–82%，RF 已接近表格特征天花板；ImageNet 预训练视觉嵌入可提供语义级变化表征。
3. **实现分工**：`torchvision` 负责 ROI 编码；`sklearn.linear_model.LogisticRegression` 负责监督分类，与 K-Means / 自研 RF 回归并存。

**为何不指望仅换 sklearn LR 而不加 CNN：** 在全量 holdout 上，手工特征 + LR 约 73.6%，与 RF ~82% 差距主要来自特征而非求解器；因此必须升级输入表征。

### 实现细节

#### CNN 嵌入抽取（`scripts/extract_xbd_cnn_embeddings.py`）

对每个建筑：

1. 用与手工特征相同的多边形 mask 裁剪 pre / post ROI；
2. ImageNet 归一化后缩放到 224×224；
3. 冻结 `ResNet18`（去掉 `fc`，输出 512 维）分别编码 pre、post；
4. 对 `(post - pre)` 归一化到 `[0,1]` 作为第三路「diff 图像」再编码；
5. 拼接为 **1536 维**，写入 NPZ。

```python
pre_emb = backbone(pre_batch)   # (B, 512)
post_emb = backbone(post_batch)
diff_emb = backbone(((post_batch - pre_batch).clamp(-1, 1) + 1) / 2)
merged = concat([pre_emb, post_emb, diff_emb], axis=1)  # (B, 1536)
```

#### 训练管线（`closed_loop_core.py`）

| 步骤 | 实现 |
|------|------|
| 加载 | `_load_xbd_feature_rows(csv, cnn_store)` → 1567 维特征 + 二分类标签 |
| 划分 | 80/20 holdout + 验证集，`_stratified_split_with_ids` |
| 全局 LR | `_fit_sklearn_logistic()`：`Pipeline(StandardScaler → PCA(≤512) → LogisticRegression)` |
| LR 超参 | `C=16`，`class_weight='balanced'`，`solver='saga'`，`max_iter=8000` |
| 局部 LR | `_train_disaster_local_logistic()`，样本 ≥1500 的灾害单独拟合 |
| 策略校准 | 保留 `DisasterAdaptiveDamageModel` + `_calibrate_disaster_strategies()`，底层模型改为 `SklearnLogisticDamageModel` |
| 阈值 | 验证集 `_tune_decision_threshold()`，评分仍以准确率为主 |

**`SklearnLogisticDamageModel` 包装类**（满足闭环 `predict_proba` / `predict_labels` 接口）：

```python
class SklearnLogisticDamageModel:
    def predict_proba_one(self, row):
        prob = self.estimator.predict_proba([row])[0]
        return float(prob[1])
```

#### 依赖变更（`requirements.txt`）

新增：`numpy`、`scikit-learn`、`torch`、`torchvision`。K-Means 与任务完成度 RF 仍为自研实现。

#### 闭环推理

`_closed_loop_optimization()` 通过 `_build_damage_feature_row(target, cnn_store, cnn_default)` 拼接特征；`run_full_train_test.py` 目标需含 `sample_id` 与 `building_index` 以便查 NPZ。

### 实验记录

| 实验 | 配置 | holdout 准确率 |
|------|------|---------------|
| 子集 10,000 条 | 冻结 ResNet18 + LR + PCA | ~79.7% |
| 全量 159,794 条 | 手工 + CNN + LR（拟合 100k） | **82.39%** |
| 全量 | 仅 CNN 1536 维 + LR | ~81.95%（手工特征仍有增益） |
| 全量闭环 benchmark | 同上 + 200 目标 × 3 轮 | **82.84%**（`requirement_report`） |

### 结果（`xbd_closed_loop_result_cnn_lr.json`）

| 指标 | 数值 | 协议 |
|------|------|------|
| **毁伤准确率** | **82.84%** | ≥ 92%（**未达标**） |
| F1 / 精确率 / 召回率 | 0.622 / 0.747 / 0.533 | — |
| 决策阈值（验证集） | 0.665 | — |
| 灾害策略数 | 10 | — |
| 态势更新时延 | 0.131 s | ≤ 1 s（**达标**） |
| 闭环目标数 | 200 | ≥ 50（**达标**） |
| SC2LE 任务完成度 | 94.88% | ≥ 90%（**达标**，模拟数据） |
| CNN 抽取 + 训练 + 闭环总耗时 | ~343 s | — |

**算法声明（输出 JSON）**：

```json
"damage_assessment": "ResNet18 ROI embeddings + logistic regression classifier",
"feature_extraction": "torchvision ResNet18 pre/post/diff embeddings (1536-d)",
"classifier": "logistic_regression"
```

### 分析与结论

- **相对 RF 路径**：82.8% vs ~82.0%，冻结 ImageNet 特征带来的提升有限，说明瓶颈仍在 **特征与 xBD 域的匹配度**（如 `hurricane-harvey`），而非分类器形式。
- **相对协议**：算法组合已对齐（LR 分类 + K-Means + RF 回归），但 **92% 准确率未达成**。
- **后续方向**（仍保持 LR 为分类器）：在 xBD 上 **微调** ResNet 后再抽 embedding + LR；或更强 backbone（ResNet50）并重新抽取；不宜再退回毁伤 RF 作为主分类器。

---

## 各阶段结果汇总

| 阶段 | 模型 | 标签体系 | 特征维 | 样本数 | holdout 准确率 | 协议达标 |
|------|------|----------|--------|--------|---------------|----------|
| 流水线 demo | 模拟 LR | 二分类 | — | 15 目标 | 0.0% | 否 |
| 基线全量 | LR | 二分类 | 6–8 | 162,787 | **73.6%** | 否（≥92%） |
| v2 特征 + LR | LR | 二分类 | 12+ | 159,794 | ~70.9% | 否 |
| 分层 + RF | RF | 二分类 | 21+ | 159,794 | ~80.3% | 否 |
| 灾害自适应 | RF 集成 | 二分类 | **31** | 159,794 | **82.0%** | 否（≥92%） |
| 三分类集成 | Multiclass RF | 三分类 | 31 | 159,794 | **78.3%** | 是（≥75%） |
| **CNN + LR** | **sklearn LR** | **二分类** | **1567→PCA** | 159,794 | **82.8%** | **否（≥92%）** |

---

## 关键代码索引

| 功能 | 路径 |
|------|------|
| 手工特征抽取 | `scripts/extract_xbd_damage_features.py` |
| **CNN 嵌入抽取** | **`scripts/extract_xbd_cnn_embeddings.py`** |
| 全量训练 + 闭环测试 | `scripts/run_full_train_test.py` |
| 毁伤模型训练 / holdout | `closed_loop_agent/closed_loop_core.py` → `_train_models()` |
| CNN 存储加载 | `_load_cnn_embedding_store()`、`_build_damage_feature_row()` |
| **LR 分类器** | **`SklearnLogisticDamageModel`**、`_fit_sklearn_logistic()` |
| 局部 LR + 策略 | `_train_disaster_local_logistic()`、`DisasterAdaptiveDamageModel` |
| 三分类（历史） | `_tier3_label()`、`MulticlassRandomForest`、`Tier3EnsembleModel` |
| 二分类 RF（历史） | `RandomForestClassifier`、`_calibrate_disaster_strategies()` |
| 闭环推理 | `_closed_loop_optimization()` |

---

## 复现命令

**抽取 CNN 嵌入（全量约 83 分钟，仅需一次）**：

```bash
cd A2A
python scripts/extract_xbd_cnn_embeddings.py --input-root data/xbd/train/train
```

**仅训练毁伤模型并输出 holdout 指标（CNN + LR，当前默认）**：

```bash
python -c "
import json, time
from closed_loop_agent.closed_loop_core import _train_models
t = time.time()
r = _train_models(20260412, {
    'xbd_damage_csv': 'data/xbd/processed/xbd_damage_features_train.csv',
    'xbd_cnn_npz': 'data/xbd/processed/xbd_cnn_embeddings_train.npz',
})
print(json.dumps({'elapsed_s': round(time.time()-t, 1), 'metrics': r['metrics']}, ensure_ascii=False, indent=2))
"
```

**完整闭环 benchmark（含 200 目标仿真）**：

```bash
python scripts/run_full_train_test.py \
  --feature-csv data/xbd/processed/xbd_damage_features_train.csv \
  --cnn-npz data/xbd/processed/xbd_cnn_embeddings_train.npz \
  --result-json data/xbd/processed/xbd_closed_loop_result_cnn_lr.json
```

**重新抽取手工 v2 特征（约 20–30 分钟）**：

```bash
python scripts/extract_xbd_damage_features.py --input-root data/xbd/train/train
```

---

## 结论与限制

**已达成的目标**：

- 全量 159,794 条样本上完成 holdout 与闭环评估；
- **迭代五**：三分类 78.3%，满足阶段性 75% 阈值；
- **迭代六**：毁伤分类器回归 **逻辑回归**，算法组合与协议一致（LR + K-Means + RF 回归）；
- 迭代六 holdout **82.84%**，为当前最高二分类成绩，但仍低于 92%；
- 态势时延（~0.13 s/周期）、目标数（200）及 SC2LE 模拟指标均满足协议。

**仍存在的限制**：

- **92% 二分类**在冻结 ImageNet ResNet18 + LR 下未达成；CNN 相对 RF 提升约 0.8 个百分点；
- `hurricane-harvey` 等灾害仍是准确率下界；
- CNN 全量抽取耗时长（~83 min），且引入 PyTorch / sklearn 依赖；
- SC2LE 基于模拟数据；xBD 官方 test 集无标签。

**后续改进方向**（保持 LR 为毁伤分类器）：

- 在 xBD 上微调 ResNet 后再抽 embedding + LR；
- 更强 backbone（ResNet50 等）并重新抽取；
- 不建议将毁伤主分类器改回随机森林（与协议「逻辑回归分类」冲突）。

---

*文档依据：各迭代代码变更、`data/xbd/processed/` 下结果 JSON（含 `xbd_closed_loop_result_cnn_lr.json`）、`_train_models(20260412)` 实测输出。*
