# multimodal_mamba_fusion_onnx

Native ONNX Runtime M15 package exported from the trained small-profile
`models/checkpoints/mamba_fusion_s.safetensors` checkpoint.

Each batch row contains four 256-dimensional slots in `eo_ir`, `sar`, `radar`,
`text_report` order. Zero-fill missing slots and set the corresponding mask value
to zero. The output is one normalized global fused embedding. Use the Python
`multimodal_mamba_fusion` package when track-specific sensor association and
target-ID attachment are required.

Regenerate M15 and M20 ONNX artifacts with:

```powershell
python scripts/export_structured_neural_onnx_models.py
```
