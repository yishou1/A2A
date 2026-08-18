# intent_gaussian_naive_bayes_onnx

Native ONNX Runtime M07 package exported from
`models/intent_gaussian_naive_bayes.joblib`.

Input rows use this exact feature order:

1. `radar_activity`
2. `communication_intensity`
3. `movement_consistency`
4. `weapon_signature`
5. `proximity_score`

Output probability columns and predicted indices use `benign`, `surveillance`,
`hostile`. Callers retain observation IDs and join them back to output rows by
position.

Regenerate both sklearn ONNX packages with:

```powershell
python scripts/export_sklearn_onnx_models.py
```
