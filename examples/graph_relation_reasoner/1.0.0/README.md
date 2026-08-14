# Graph Relation Reasoner

Graph reasoning algorithm package for **M07 图神经网络 / graph relation reasoning**.

It constructs spatiotemporal relation edges and connected group candidates from structured tracks using distance, heading difference, and speed difference. The frozen ST-GNN trajectory bundles under `models/track_threat/` use the same graph-oriented deployment boundary.

This is not a knowledge-graph/RAG/semantic-intent module. It only builds track-to-track spatial graph relations for formation/group detection and trajectory prediction support.
