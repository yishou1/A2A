# threat_priority_random_forest_onnx

Native ONNX Runtime M05 package exported from
`models/threat_priority_random_forest.joblib`.

Input rows use this exact feature order:

1. `threat_score`
2. `distance_km`
3. `speed_mps`
4. `asset_value`
5. `intel_confidence`

Output probability columns and predicted indices use `low`, `medium`, `high`.
The ONNX package intentionally accepts tensors rather than target objects; callers
retain target IDs and join them back to output rows by position.

Regenerate both sklearn ONNX packages with:

```powershell
python scripts/export_sklearn_onnx_models.py
```
