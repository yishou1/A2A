# supcon_meta_classifier_onnx

Native ONNX Runtime M06 package exported from the trained small-profile
`models/checkpoints/supcon_meta_s.safetensors` checkpoint.

Input is a batch of 256-dimensional fused embeddings. Output class order is
`friendly`, `neutral`, `hostile`, `unknown`. Target IDs are joined back to rows
by the caller. This portable variant uses the checkpoint's frozen prototypes;
use `supcon_meta_classifier` when request-specific support shots are required.

Regenerate M06 and M14 ONNX artifacts with:

```powershell
python scripts/export_torch_classifier_onnx_models.py
```
