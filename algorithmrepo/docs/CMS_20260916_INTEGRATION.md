# CMS 2026-09-16 算法增量整合说明

来源：`origin/cms/tactical-intelligence-agent` 的 `51106f55`。当前集成仓库以 `algorithmrepo/` 为算法库根目录，因此采用路径映射移植，不直接合并该分支的仓库根目录。

## 已整合

- `algorithms/` 的 11 个独立 Python 算法入口、I/O 契约、JSON Schema、三档算力 API 与抗干扰 MCDM/SAW 路由，放在 `algorithmrepo/algorithms/`。
- `config/profiles/small.yaml`、`medium.yaml`、`large.yaml` 的检测同权重和 low/mid/high 参数。原有 `marl_checkpoint` 保留；low 档的 EDL、Mamba、SupCon、MARL-PPO 继续指向当前仓库已交付并可校验的 `_s.safetensors`，避免新分支未交付 `.pt` 导致回归。
- RT-DETR 推理尊重 `odconv_enable`、`detection_half`（仅 CUDA）和 `detection_max_det`；运行时报告不会把明确禁用的 ODConv 说成已启用。2026-10-16 合并远端后，三档统一使用 97 类发布别名 `models/checkpoints/battlefield_rtdetr.pt`；缺失时默认报错。通用 `rtdetr-l.pt` 仅在显式设置 `detection_fallback_model` 时使用，并报告实际模型文件。
- 已接入师弟交付的 `unit17_ap_finetune_upload_20260916` 发布包安装流程。`scripts/install_rtdetr_release_bundle.ps1` 会校验 `MANIFEST.json` 后安装 97 类 best/last PT 和 ONNX；重复别名优先使用硬链接，生产大文件被 `.gitignore` 精确排除。
- 已加入 `examples/battlefield_rtdetr_detector_onnx/1.0.0/` 的可提交元数据、97 类标签、输入输出契约和紧凑 golden case。ONNX Runtime 真实验收已通过。

## 仍需注意

- PT/ONNX 是外部交付的大文件，不随普通 Git 提交传播。新设备 clone 后必须取得发布包并执行安装脚本；缺少 97 类权重时，默认配置与 `-RealRTDETRDemo` 启动模式都会报错，避免把通用模型误当成 97 类模型演示。
- 当前设备已安装并校验 `battlefield_rtdetr_unit17_coco25_ap_kaggle_best.pt`（SHA-256 `cb7092f2...ae7cec`）和 `battlefield_rtdetr.onnx`（SHA-256 `b6123191...5fc97`）。端口 9020 的真实 HTTP 烟雾测试返回 `runtime.mode=real`、上述 best 权重路径和 6 个检测结果，包含 97 类标签中的 `ship`。
- `motr_tracker_battlefield.pt`、ODConv 与 mid/high 认知侧权重同样未随此次 cms Git 提交；这些档位的参数配置不等于所有神经网络后端已可真实运行。
- 现有 HTTP 服务、算法注册表和 AMOS 仍保持原有 ID/路由；新增的 `anti_jam_mcdm_router` 当前可通过 Python `algorithms.invoke` 单独调用，尚未新增 HTTP 服务或自动激活到前端。旧 `marl_dynamic_router` 暂保留兼容，不在此增量中删除。

## 最小核验

在 `algorithmrepo/` 目录、已安装 Python 依赖的环境中：

```powershell
python -m algorithms --list-contracts
python -m algorithms --list-tiers
python -m pytest tests/python/test_cms_algorithm_tiers.py -q
```

首次安装师弟交付的真实检测权重：

```powershell
# 从仓库根目录执行；默认查找相邻的 release_bundles 目录
.\algorithmrepo\scripts\install_rtdetr_release_bundle.ps1

# 只启动真实 RT-DETR HTTP 服务（端口 9020）
.\algorithmrepo\scripts\start_a2a_algorithm_services.ps1 `
  -Python D:\software\Miniconda3\envs\algorithm_repo\python.exe `
  -RealRTDETRDemo
```

本次是**算法内容移植**，没有创建 Git merge commit；当前分支其他未提交改动没有被重置或覆盖。
