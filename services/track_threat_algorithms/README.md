# Track Threat Algorithms Service

Python HTTP service for the track-threat algorithm packages.

Run:

```bash
pip install -r services/requirements.txt
python services/track_threat_algorithms/app/main.py
```

Default port: `9022`.

Mounted algorithm endpoints:

- `/target_type_classifier/health`, `/target_type_classifier/metadata`, `/target_type_classifier/predict`
- `/trajectory_predictor/health`, `/trajectory_predictor/metadata`, `/trajectory_predictor/predict`
- `/multimodal_feature_fuser/health`, `/multimodal_feature_fuser/metadata`, `/multimodal_feature_fuser/predict`
- `/track_state_updater/health`, `/track_state_updater/metadata`, `/track_state_updater/predict`
- `/graph_relation_reasoner/health`, `/graph_relation_reasoner/metadata`, `/graph_relation_reasoner/predict`
