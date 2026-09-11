# Battlefield RT-DETR Detector ONNX

本文件夹可直接用于 GitHub 上传（整包在上级 `onnx_github_upload/`，请先读那里的 `README.md`）。

| 文件 | 说明 |
|------|------|
| `1.0.0/` | 完整 ONNX 算法包（含 `model.onnx` ≈ 125.6 MB） |
| `RT_DETR_ONNX_封装报告.md` | 中文封装报告 |
| `RT_DETR_ONNX_PACKAGING_REPORT.md` | 同上（ASCII 文件名副本） |
| `export_rtdetr_onnx.py` | 从 A2A 根目录重导脚本 |

**推送前必须启用 Git LFS**，否则 GitHub 会拒绝超 100MB 的 `model.onnx`。
