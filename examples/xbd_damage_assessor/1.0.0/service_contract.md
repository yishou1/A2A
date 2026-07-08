# xBD Damage Assessor service contract

## POST /predict

### Features mode

```json
{
  "algorithm_id": "xbd_damage_assessor",
  "version": "1.0.0",
  "inputs": {
    "input_mode": "features",
    "sample_id": "guatemala-volcano_00000000",
    "handcrafted_features": {
      "pre_area": 0.0069561004638671875,
      "spectral_delta": 0.11385739196510551,
      "texture_delta": 0.040614633569358925,
      "heat_signature": 0.10955983161018899,
      "crater_density": 0.29558541266794625,
      "std_spectral": 0.09248520384250485,
      "max_spectral": 0.615686274509804,
      "high_change_ratio": 0.21565670414038937,
      "severe_damage_ratio": 0.06073485056210584,
      "collapse_ratio": 0.17918837400603235,
      "post_brightness": 0.6580762055301864,
      "brightness_drop": 0.0601235503798625,
      "normalized_distance": 0.411391608396209,
      "detection_confidence": 1.0,
      "threat_score": 0.5
    }
  }
}
```

### Images mode

Requires `polygon`. Missing polygon returns `assessment_status=insufficient_data`.

## Output

```json
{
  "damage_probability": 0.72,
  "damage_label": 1,
  "damage_result": "damaged",
  "assessment_status": "model_estimate"
}
```
