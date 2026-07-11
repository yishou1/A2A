# Deployable Trajectory Models

This directory stores small trained model artifacts that can be loaded by the
online Track Threat Agent.

Current models:

- `trajectory_predictor_ais.json`
  - Model type: NumPy ridge sequence predictor.
  - Training data: processed TrAISformer / DMA AIS `ct_dma_valid` samples.
  - Input: 6 historical AIS trajectory points.
  - Output horizons: 600 seconds and 1200 seconds.
  - Safety boundary: trajectory prediction only; no weapon control or
    engagement advice.
- `trajectory_predictor_aircraft_short.json`
  - Model type: NumPy ridge sequence predictor.
  - Training data: short-horizon OpenSky ADS-B samples.
  - Used by the online Agent when no explicit
    `TRACK_THREAT_LEARNED_MODEL_PATH` is configured.

ST-GNN training data, PyTorch checkpoints, training code, and evaluation
reports are stored in the separate `/Users/mac/Desktop/st-gnn-trajectory-training`
project. A released ST-GNN bundle is discovered with `ST_GNN_MODEL_DIR`; it is
not copied into this directory by default.
