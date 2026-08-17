#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_NAME="${A2A_ENV_NAME:-a2a}"
TORCH_INDEX_URL="${A2A_TORCH_INDEX_URL:-https://download.pytorch.org/whl/cpu}"
BUILD_JOBS="${A2A_BUILD_JOBS:-2}"

if ! command -v conda >/dev/null 2>&1; then
  echo "Conda was not found. Install Miniforge or Anaconda first." >&2
  exit 1
fi

cd "$ROOT_DIR"

if conda env list | awk -v name="$ENV_NAME" '$1 == name { found = 1 } END { exit !found }'; then
  echo "[environment] updating Conda environment '$ENV_NAME'"
  conda env update --name "$ENV_NAME" --file environment.yml
else
  echo "[environment] creating Conda environment '$ENV_NAME'"
  conda env create --name "$ENV_NAME" --file environment.yml
fi

conda run -n "$ENV_NAME" python -m pip install --upgrade pip setuptools wheel

if ! conda run -n "$ENV_NAME" python -c 'import torch, torchvision' >/dev/null 2>&1; then
  echo "[dependencies] installing CPU PyTorch"
  conda run -n "$ENV_NAME" python -m pip install \
    torch torchvision --index-url "$TORCH_INDEX_URL"
else
  echo "[dependencies] keeping the installed PyTorch runtime"
fi

echo "[dependencies] installing the integrated Python runtime"
conda run -n "$ENV_NAME" python -m pip install -r requirements.txt
conda run -n "$ENV_NAME" python -m pip check

echo "[algolib] configuring and building the C++ service"
conda run -n "$ENV_NAME" cmake -S commander -B commander/build -G Ninja \
  -DALGOLIB_BUILD_TESTS=ON -DALGOLIB_WITH_ONNXRUNTIME=OFF
conda run -n "$ENV_NAME" cmake --build commander/build -j "$BUILD_JOBS"

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "[configuration] created .env from .env.example"
fi

conda run -n "$ENV_NAME" python -c \
  'import amos_platform, fastapi, flask, onnxruntime, torch; print("Python runtime imports: OK")'
test -x commander/build/algolib
test -x commander/build/algolib_server

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  echo "[docker] Docker Engine connection: OK"
else
  echo "[docker] Docker Engine is not available yet. Enable Docker Desktop WSL Integration before starting the system."
fi

echo
echo "Environment is ready. Review .env, then run:"
echo "  ./scripts/start.sh --offline"
echo "or:"
echo "  ./scripts/start.sh --require-llm"
