# Service Contract

- GET `/target_type_classifier/health`
- GET `/target_type_classifier/metadata`
- POST `/target_type_classifier/predict`

Port: `9022`

Algorithm class: `M04 特征编码与分类`

Input: structured `observations`, `detections`, or `tracks`.

Output: `classifications[]` with object type, confidence, type scores, and evidence.

Safety boundary: classification is used only for simulation situation awareness.
