# Track Threat Frozen Model Bundles

This directory contains small frozen inference bundles for the track-threat trajectory prediction algorithms.

Committed files are deployable inference artifacts only:

- `model.ts`
- `model_manifest.json`
- `normalization.json`
- `metrics.json`
- `golden_io.json`
- `sha256sums.json`

Training data, optimizer checkpoints, TensorBoard logs, and Kaggle archives are intentionally excluded.

## Bundles

### `st_gnn_ship_kaggle_v1`

Release status: `gate_passed`.

Independent test metrics:

- ADE: 204.84 m
- FDE: 283.55 m
- calibrated 90% coverage: 0.9293
- strongest physical baseline: constant velocity
- ADE improvement over strongest baseline: 53.25%
- FDE improvement over strongest baseline: 53.11%

### `st_gnn_aircraft_kaggle_v1`

Release status: `gate_passed`.

Independent test metrics:

- ADE: 318.68 m
- FDE: 654.18 m
- calibrated 90% coverage: 0.9026
- strongest physical baseline: IMM
- ADE improvement over strongest baseline: 13.96%
- FDE improvement over strongest baseline: 18.43%

Both bundles were evaluated on isolated test splits. Uncertainty scaling was fitted on validation data only. Each connected golden graph verifies that the exported TorchScript includes the graph-message path.

Safety boundary: these model outputs are used only for simulation trajectory prediction and situation-awareness ranking. They do not produce weapon control, guidance, or engagement decisions.
