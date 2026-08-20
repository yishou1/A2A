# Service Contract

- GET `/trajectory_predictor/health`
- GET `/trajectory_predictor/metadata`
- POST `/trajectory_predictor/predict`

Port: `9022`

Algorithm class: `M06 时间序列预测`

Input: structured tracks with history paths.

Output: `predictions[].predicted_path` with horizon, position, uncertainty, model version, and fallback status.

Safety boundary: trajectory prediction only; no task planning, weapon control, guidance, or engagement advice.
