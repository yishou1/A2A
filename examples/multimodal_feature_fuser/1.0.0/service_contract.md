# Service Contract

- GET `/multimodal_feature_fuser/health`
- GET `/multimodal_feature_fuser/metadata`
- POST `/multimodal_feature_fuser/predict`

Port: `9022`

Algorithm class: `M03 多模态融合`

Input: structured `detections`, `tracks`, `scene`, and `protected_assets`.

Output: `feature_vectors` with numeric kinematic/context features and source counts.

Safety boundary: structured feature fusion only; no KG/RAG, task planning, weapon control, or engagement advice.
