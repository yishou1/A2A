# Target Trend Predictor ONNX

Native ONNX package for the bootstrap fixed-window target-trend model. The
model consumes exactly twelve history steps, with four features per step:

1. `risk_score`
2. `probability`
3. `inverse_priority`
4. `resource_pressure`

This package only performs trend scoring. History collection, resampling, and
decision orchestration remain in `decision_planning_core`.

The model is copied from `models/decision_planning_lstm.onnx`. Its metadata
marks it as a bootstrap model rather than a production-data-trained model.

