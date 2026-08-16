# Graph Relation Reasoner

Graph reasoning algorithm package for **M20 图神经网络**. It uses a trained
dense message-passing GNN to classify pairwise formation-membership edges from
structured track position, motion, altitude, confidence and object-type features.
Connected components over accepted edges form group candidates.

The model performs two message-passing rounds over a dense track graph and then
uses a learned symmetric edge head. It does not fall back to the previous
distance/heading/speed threshold rule when its checkpoint is unavailable.

## Reference evaluation

| Method | Accuracy | Precision | Recall | F1 | ROC AUC |
|---|---:|---:|---:|---:|---:|
| Trained GNN | 0.992088 | 0.940023 | 0.986683 | 0.962788 | 0.999212 |
| Distance/motion rule | 0.982481 | 0.947815 | 0.879540 | 0.912402 | 0.936968 |

The holdout contains 600 independently generated formation graphs. Rebuild the
checkpoint, data and metadata deterministically with:

```powershell
python scripts/train_graph_relation_gnn.py
```

These synthetic labels verify the GNN training and deployment path; they are not
evidence of operational formation-intelligence accuracy. Real deployment needs
time-separated, human-reviewed multi-target relation labels.

This is not a knowledge-graph/RAG/semantic-intent module and does not make task,
engagement or weapon-control decisions.
