# Service Contract

- GET `/graph_relation_reasoner/health`
- GET `/graph_relation_reasoner/metadata`
- POST `/graph_relation_reasoner/predict`

Port: `9022`

Algorithm class: `M07 图神经网络`

Input: structured `tracks` with lat/lon/speed/heading/object_type.

Output: graph `relations`, connected `groups`, and graph summary.

Safety boundary: spatiotemporal relation graph reasoning only; no KG/RAG semantic reasoning, task planning, weapon control, or engagement advice.
