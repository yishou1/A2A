# Target Trend Predictor ONNX

Native ONNX package for a trained fixed-window target-trend LSTM. The model
consumes exactly twelve ordered history steps and predicts the normalized risk
score four steps beyond the input window. Each step has four features:

1. `risk_score`
2. `probability`
3. `inverse_priority`
4. `resource_pressure`

Inputs must already be normalized to `[0, 1]`; histories with another length
must be resampled before invocation. This package only performs trend scoring.
History collection, resampling, and decision orchestration remain outside the
package.

## Reference evaluation

The deterministic training generator creates smooth, noisy target-risk
trajectories with varying slope, curvature and periodic components. The target
is the latent risk four steps after the twelve-step observation window. On an
independent 800-sequence holdout set:

| Method | MAE | RMSE | R2 |
|---|---:|---:|---:|
| Trained LSTM | 0.036445 | 0.051080 | 0.980410 |
| Last observation | 0.071554 | 0.095422 | 0.931635 |
| Historical mean | 0.174567 | 0.209907 | 0.669181 |

Rebuild the model, datasets, metadata and golden result with:

```powershell
python scripts/train_target_trend_predictor.py
```

The generated dataset is synthetic and validates the engineering/training
pipeline only. It is not evidence of operational target-forecasting accuracy;
deployment requires retraining and independent evaluation on representative,
time-ordered sensor histories.

