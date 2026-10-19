# Unified Algorithm Acceptance

Use `scripts/accept_all_algorithms.py` as the single acceptance entry point for
all algorithm packages. The script does not start services by itself, so its
default mode will not create Python windows or change running processes.

## Static acceptance

```powershell
python scripts/accept_all_algorithms.py
```

This checks cards, referenced files, JSON schemas, golden case pairs, model
package files, recommended metadata, and duplicate HTTP endpoints.

Warnings are advisory in the default mode and do not change a package from
`PASS` to `FAIL`. For example, `status: draft` is reported as a warning. The
summary prints both the number of passing packages and the total warning count.

## Runtime acceptance

Start the required HTTP service group first, then run:

```powershell
python scripts/accept_all_algorithms.py --runtime
```

Native ONNX packages are executed in-process with CPU ONNX Runtime. HTTP
packages are checked through `/health`, `/metadata`, and `/predict`.

Because the current service map has port conflicts, validate service groups
separately:

```powershell
python scripts/accept_all_algorithms.py --runtime --backend onnx
python scripts/accept_all_algorithms.py --runtime --algorithm marl_ppo_task_scheduler
```

## Algorithm-library CLI acceptance

Use an `algolib` build linked to the real ONNX Runtime SDK for native ONNX
packages:

```powershell
python scripts/accept_all_algorithms.py `
  --runtime `
  --algolib .\build\Release\algolib.exe
```

The script uses an isolated registry under the report directory and performs
`register`, `activate`, and `run` for each selected package.

To verify all native ONNX packages through both Python ONNX Runtime and the
C++ CLI in one command, use the batch group with a real-runtime build:

```powershell
.\scripts\run_algorithm_batch_acceptance.ps1 `
  -Group onnx-all `
  -Python D:\software\Miniconda3\envs\algorithm_repo\python.exe `
  -Algolib .\build-smoke-offline\Debug\algolib.exe `
  -RealGate
```

`onnx_text_classifier` is a deterministic constant-logit integration fixture.
It verifies tokenization, ONNX execution, and classification postprocessing;
it is not a trained semantic text classifier.

## Strict acceptance

```powershell
python scripts/accept_all_algorithms.py --strict
```

Strict mode promotes draft lifecycle status and missing recommended card fields
from warnings to failures.

## Real-mode acceptance gate

Use `--forbid-fallback` together with `--runtime` when the result must prove a
real implementation instead of interface compatibility:

```powershell
python scripts/accept_all_algorithms.py `
  --runtime `
  --forbid-fallback `
  --algorithm xbd_damage_assessor
```

The real-mode gate requires HTTP health responses to explicitly report
`model_loaded=true`. It fails when runtime or algolib outputs contain Mock,
stub, fallback, placeholder, or random-initialization markers.

For TIA services, the batch runner has separate Mock and real groups:

```powershell
.\scripts\run_algorithm_batch_acceptance.ps1 -Group tia-mock
.\scripts\run_algorithm_batch_acceptance.ps1 -Group tia-real
```

`tia-real` sets `TIA_USE_MOCK=0` and enables the real-mode gate automatically.
The lower-level TIA verifier also returns a non-zero exit code when real models
or required checkpoints are incomplete:

```powershell
python scripts/verify_tia_algorithms.py --mode real
```

The M01 clustering package also has a dedicated group. It starts the service,
checks both K-Means and DBSCAN golden cases through HTTP, and runs the C++ CLI
register/activate/run path:

```powershell
.\scripts\run_algorithm_batch_acceptance.ps1 `
  -Group m01-clustering `
  -Python D:\software\Miniconda3\envs\algorithm_repo\python.exe `
  -RealGate
```

The M02 association-rule package verifies the persisted rules artifact hash,
independent reference-policy holdout metrics, HTTP prediction, and the C++ CLI:

```powershell
.\scripts\run_algorithm_batch_acceptance.ps1 `
  -Group m02-association `
  -Python D:\software\Miniconda3\envs\algorithm_repo\python.exe `
  -RealGate
```

The M03 linear-regression package verifies per-request OLS parameters,
implementation/data hashes, synthetic holdout metrics, HTTP, and the C++ CLI:

```powershell
.\scripts\run_algorithm_batch_acceptance.ps1 `
  -Group m03-linear-regression `
  -Python D:\software\Miniconda3\envs\algorithm_repo\python.exe `
  -RealGate
```

The M04 group runs both trained logistic-regression ONNX packages through
Python ONNX Runtime and the real C++ ONNX Runtime CLI:

```powershell
.\scripts\run_algorithm_batch_acceptance.ps1 `
  -Group m04-logistic-regression `
  -Python D:\software\Miniconda3\envs\algorithm_repo\python.exe `
  -Algolib .\build-smoke-offline\Debug\algolib.exe `
  -RealGate
```

The persisted M05 random-forest package uses the same strict path and verifies
the model artifact hash during service readiness:

```powershell
.\scripts\run_algorithm_batch_acceptance.ps1 `
  -Group m05-random-forest `
  -Python D:\software\Miniconda3\envs\algorithm_repo\python.exe `
  -RealGate
```

The M06 group forces `TIA_USE_MOCK=0`, loads the trained small-profile
SupCon/prototypical neural checkpoint, and verifies HTTP plus the C++ CLI:

```powershell
.\scripts\run_algorithm_batch_acceptance.ps1 `
  -Group m06-neural-network `
  -Python D:\software\Miniconda3\envs\algorithm_repo\python.exe `
  -RealGate
```

The M13 group forces `TIA_USE_MOCK=0`, validates the trained small-profile
MARL-PPO Actor-Critic checkpoint, and exercises both sensor and strike
allocation through HTTP and the C++ CLI:

```powershell
.\scripts\run_algorithm_batch_acceptance.ps1 `
  -Group m13-reinforcement-learning `
  -Python D:\software\Miniconda3\envs\algorithm_repo\python.exe `
  -Algolib .\build-smoke-offline\Debug\algolib.exe `
  -RealGate
```

The M14 group loads the calibrated EDL checkpoint, rejects random initialization,
and validates partitioned decisions, uncertainty evidence, and the human-review
queue through HTTP and the C++ CLI:

```powershell
.\scripts\run_algorithm_batch_acceptance.ps1 `
  -Group m14-explainable-ai `
  -Python D:\software\Miniconda3\envs\algorithm_repo\python.exe `
  -Algolib .\build-smoke-offline\Debug\algolib.exe `
  -RealGate
```

The M15 group loads the trained Mamba-style embedding-fusion checkpoint, rejects
random initialization and fallback, and validates unequal-length modality vectors
plus explicit track-to-sensor association through HTTP and the C++ CLI:

```powershell
.\scripts\run_algorithm_batch_acceptance.ps1 `
  -Group m15-multimodal-fusion `
  -Python D:\software\Miniconda3\envs\algorithm_repo\python.exe `
  -Algolib .\build-smoke-offline\Debug\algolib.exe `
  -RealGate
```

The M16 group validates the trained fixed-window LSTM ONNX artifact and runs its
golden sequence through both Python ONNX Runtime and the C++ CLI:

```powershell
.\scripts\run_algorithm_batch_acceptance.ps1 `
  -Group m16-time-series `
  -Python D:\software\Miniconda3\envs\algorithm_repo\python.exe `
  -Algolib .\build-smoke-offline\Debug\algolib.exe `
  -RealGate
```

The M20 group loads the trained message-passing GNN, rejects a missing or
hash-mismatched checkpoint without rule fallback, and validates learned edge
relations through HTTP and the C++ CLI:

```powershell
.\scripts\run_algorithm_batch_acceptance.ps1 `
  -Group m20-graph-neural-network `
  -Python D:\software\Miniconda3\envs\algorithm_repo\python.exe `
  -Algolib .\build-smoke-offline\Debug\algolib.exe `
  -RealGate
```

The M07 Gaussian Naive Bayes package verifies the frozen probabilistic model
and its posterior-probability golden case:

```powershell
.\scripts\run_algorithm_batch_acceptance.ps1 `
  -Group m07-naive-bayes `
  -Python D:\software\Miniconda3\envs\algorithm_repo\python.exe `
  -RealGate
```

The M12 group performs a real FedAvg round with nested client tensors and
verifies the reproducible multi-client experiment artifacts:

```powershell
.\scripts\run_algorithm_batch_acceptance.ps1 `
  -Group m12-fedavg `
  -Python D:\software\Miniconda3\envs\algorithm_repo\python.exe `
  -RealGate
```

The M08 group loads the trained PyTorch conditional generator and checks seeded
synthetic generation through both HTTP and the C++ CLI:

```powershell
.\scripts\run_algorithm_batch_acceptance.ps1 `
  -Group m08-gan `
  -Python D:\software\Miniconda3\envs\algorithm_repo\python.exe `
  -RealGate
```

Reports are written to:

```text
build/acceptance/acceptance_report.json
build/acceptance/acceptance_report.md
```
