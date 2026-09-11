# Battlefield RT-DETR ONNX — GitHub 上传包

自包含目录，可直接推到 GitHub（需 **Git LFS**，因 `model.onnx` ≈ **125.6 MB**，超过 GitHub 普通文件 100MB 限制）。

## 目录结构

```text
battlefield_rtdetr_detector_onnx/
  README.md
  RT_DETR_ONNX_封装报告.md          # 中文报告
  RT_DETR_ONNX_PACKAGING_REPORT.md # 同上（ASCII 文件名）
  export_rtdetr_onnx.py
  1.0.0/                           # ONNX 算法包（含 model.onnx）
```
## 方式 A：作为独立仓库推送（推荐单独开 repo）

在本目录的**上一级** `onnx_github_upload/` 操作：

```powershell
cd d:\a2a_project\A2A-main\onnx_github_upload

git init
git lfs install
git lfs track "*.onnx"
git add .gitattributes
git add battlefield_rtdetr_detector_onnx
git commit -m "Add battlefield RT-DETR ONNX algorithm package"

# 在 GitHub 新建空仓库后：
git branch -M main
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```

## 方式 B：并入现有 A2A 仓库再推送

仓库根目录已准备 `.gitattributes`（LFS 跟踪本目录下的 `*.onnx`）。

```powershell
cd d:\a2a_project\A2A-main
git lfs install
git add .gitattributes onnx_github_upload
git commit -m "Add RT-DETR ONNX GitHub upload bundle"
git push
```

## 注意

1. **必须先** `git lfs install` 再 `git add` 大文件，否则 push 会被 GitHub 拒绝。
2. `model.onnx` 与仓库内 `models/battlefield_rtdetr.onnx` 可为同一硬链接，改一边两边都会变。
3. 重新导出：在 A2A 根目录执行  
   `.\.venv\Scripts\python.exe scripts\export_rtdetr_onnx.py --force`  
   再把新 `model.onnx` 拷回本目录 `1.0.0/`。
4. 契约：`images[1,3,640,640]` → `output0[1,300,6]`（xyxy + conf + cls）；业务编排仍用 HTTP 包 `battlefield_rtdetr_detector`。
