# graph_relation_reasoner_onnx

Native ONNX Runtime M20 package exported from the trained
`models/checkpoints/graph_relation_gnn_s.safetensors` checkpoint.

Inputs are padded tensors for graphs with at most ten nodes. The output contains
the trained model's symmetric relation logits and probabilities. Callers retain
node IDs and ignore padded rows according to `node_mask`. Use the Python
`graph_relation_reasoner` package when raw geographic tracks must be converted,
thresholded, labelled, and grouped automatically.

Regenerate M15 and M20 ONNX artifacts with:

```powershell
python scripts/export_structured_neural_onnx_models.py
```
