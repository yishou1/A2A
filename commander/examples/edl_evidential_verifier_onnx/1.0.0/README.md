# edl_evidential_verifier_onnx

Native ONNX Runtime M14 package exported from the trained small-profile
`models/checkpoints/edl_head_s.safetensors` checkpoint.

Input columns are detector confidence, normalized bounding-box width, height,
center x, center y, and damage score. Output class order is `rejected`,
`verified`. This graph returns the learned evidential tensors. The caller applies
the documented policy thresholds; use `edl_evidential_verifier` for automatic
feature extraction, aleatoric uncertainty, decisions, and review-queue routing.

Regenerate M06 and M14 ONNX artifacts with:

```powershell
python scripts/export_torch_classifier_onnx_models.py
```
