# Deployable Trajectory Models

This directory contains only online inference artifacts:

- `track_threat/`: verified ST-GNN TorchScript bundles for aircraft and ship prediction.

Physics fallback is implemented in the online tracker and is not represented as
a trained model bundle. Legacy NumPy Ridge and fixed-weight graph predictors are
not part of the Agent runtime.
- `track_threat/`: embedded aircraft and ship TorchScript ST-GNN bundles.

Training data, PyTorch checkpoints, optimizer state, TensorBoard logs and
evaluation workspaces remain in the separate `st-gnn-trajectory-training`
project and are not committed with the Agent. Bundle contents and release
status are documented in `track_threat/README.md`.

All outputs are limited to trajectory prediction and situation-awareness
ranking. They do not provide weapon control, guidance or engagement decisions.
