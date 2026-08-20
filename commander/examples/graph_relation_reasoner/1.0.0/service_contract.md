# Service Contract

- GET `/graph_relation_reasoner/health`
- GET `/graph_relation_reasoner/metadata`
- POST `/graph_relation_reasoner/predict`

Port: `9038`

Algorithm class: `M20 图神经网络`

Input: structured `tracks` with lat/lon/speed/heading/object_type.

Output: learned graph `relations`, connected `groups`, graph summary, and the
checkpoint identity used for inference.

The service requires `models/checkpoints/graph_relation_gnn_s.safetensors` and
returns `model_loaded=false` when the artifact or its recorded SHA256 is invalid.
No distance-rule fallback is used.

Safety boundary: spatiotemporal relation graph reasoning only; no KG/RAG semantic reasoning, task planning, weapon control, or engagement advice.
