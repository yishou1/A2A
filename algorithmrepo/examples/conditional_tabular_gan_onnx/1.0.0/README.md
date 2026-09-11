# conditional_tabular_gan_onnx

Native ONNX Runtime M08 package exported from
`models/conditional_tabular_gan_generator.pt`.

Supply eight-dimensional noise and one-hot conditions in `benign`,
`surveillance`, `hostile` order. Output columns are `radar_activity`,
`communication_intensity`, `movement_consistency`, `weapon_signature`, and
`proximity_score`. Outputs are synthetic development data, not real sensor
observations. Use `conditional_tabular_gan` for seed/temperature handling and
named output objects.

Regenerate the M08 and M13 artifacts with:

```powershell
python scripts/export_policy_generative_onnx_models.py
```
