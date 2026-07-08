# A2A Execution Control and Closed-Loop Algorithms

This branch packages algorithms from the A2A `zh` branch as standalone `python_http_service` algorithm packages.

## Algorithms

| algorithm_id | Port | Description |
|---|---:|---|
| `execution_rule_matcher` | 9010 | Association-rule matching for execution control |
| `trajectory_linear_predictor` | 9011 | Linear-regression track prediction |
| `execution_control_planner` | 9012 | Rule matching + motion prediction + command synthesis |
| `mission_feature_adapter` | 9013 | mission_features_v2 seven-dimensional adapter |
| `mission_completion_scorer` | 9014 | Frozen SC2LE proxy random forest scorer |
| `closed_loop_decision_advisor` | 9015 | Rule-based closed-loop action advisor |
| `xbd_damage_assessor` | 9016 | Frozen xBD damage assessor (features or images + polygon) |

## Start services

```powershell
pip install -r services/requirements.txt
./scripts/start_a2a_algorithm_services.ps1
```

## Register / activate / run

```powershell
cmake -S . -B build
cmake --build build

./build/algolib.exe register ./examples/mission_completion_scorer/1.0.0
./build/algolib.exe activate mission_completion_scorer 1.0.0 python_http_service
./build/algolib.exe show-card mission_completion_scorer 1.0.0 python_http_service
./build/algolib.exe run ./examples/mission_completion_scorer/1.0.0/golden_cases/case_001_request.json
```

## Python tests

```powershell
pip install -r services/requirements.txt
pip install pytest
pytest tests/python/test_a2a_algorithm_services.py -q
```

## Notes

- `models/sc2le_proxy_mission_model.pkl` is a frozen proxy model trained without Result/completion leakage.
- `models/xbd_damage_classifier.pkl` is a frozen xBD damage classifier trained offline from handcrafted features plus ResNet18 embeddings.
- `data/execution_control/processed/mined_rules.json` is mined from fixture training records, not from TigerClaw or VisDrone datasets.
- Services do not depend on Commander, BPEL, Nacos, or A2A Agent runtime.