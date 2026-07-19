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

## Strict acceptance

```powershell
python scripts/accept_all_algorithms.py --strict
```

Strict mode promotes draft lifecycle status and missing recommended card fields
from warnings to failures.

Reports are written to:

```text
build/acceptance/acceptance_report.json
build/acceptance/acceptance_report.md
```
