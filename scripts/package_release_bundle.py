#!/usr/bin/env python3
"""Create a fully offline release bundle for the integrated demo.

The bundle contains:
  - a working-tree snapshot of the repository
  - the local Qwen model under local_models/qwen3-1.7b
  - a conda-pack archive of the current a2a environment
  - Docker image tarballs for nacos and httpbin
  - helper scripts to unpack, build, and run the demo offline

This script is intended to run on an online machine. The target machine may
remain offline after the bundle is transferred.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from textwrap import dedent


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "release_bundle"
DEFAULT_ENV_NAME = os.environ.get("A2A_ENV_NAME", "a2a")
DEFAULT_QWEN_DIR = ROOT / "local_models" / "qwen3-1.7b"
DEFAULT_REPO_NAME = "repo"
DEFAULT_ENV_ARCHIVE = "a2a-conda-pack.tar.gz"

DOCKER_IMAGES = (
    ("nacos/nacos-server:v2.4.3", "nacos_nacos-server_v2.4.3.tar"),
    ("kennethreitz/httpbin:latest", "kennethreitz_httpbin_latest.tar"),
)

RSYNC_EXCLUDES = (
    ".git/",
    ".env",
    ".runtime/",
    ".a2a_state/",
    ".pytest_cache/",
    "__pycache__/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".venv/",
    "venv/",
    "env/",
    "build/",
    "release_bundle/",
    "release_bundle.tar.gz",
    "local_models/",
    "*.pyc",
    "*.pyo",
    "*.log",
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(cmd, cwd=cwd, check=True)


def ensure_clean_output_dir(output_dir: Path) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)


def copy_repo_snapshot(repo_dir: Path) -> None:
    if not shutil.which("rsync"):
        raise RuntimeError("rsync is required to create the release bundle.")
    repo_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["rsync", "-a"]
    for pattern in RSYNC_EXCLUDES:
        cmd.extend(["--exclude", pattern])
    cmd.extend([f"{ROOT}/", f"{repo_dir}/"])
    run(cmd)


def ensure_conda_pack(env_name: str) -> None:
    probe = [
        "conda",
        "run",
        "-n",
        env_name,
        "python",
        "-c",
        "import conda_pack",
    ]
    if subprocess.run(probe, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
        print(f"[deps] installing conda-pack into environment {env_name!r}")
        run(["conda", "run", "-n", env_name, "python", "-m", "pip", "install", "conda-pack"])


def pack_conda_env(env_name: str, env_archive: Path) -> None:
    ensure_conda_pack(env_name)
    env_archive.parent.mkdir(parents=True, exist_ok=True)
    print(f"[env] packing Conda environment {env_name!r} -> {env_archive}")
    # The development environment installs the two in-repo Python packages
    # (commander and amos-platform) in editable mode.  The offline bundle also
    # contains a full repository snapshot, and the generated setup/start scripts
    # point Python at that snapshot, so editable package files do not need to be
    # embedded into the packed Conda environment.
    run(
        [
            "conda",
            "run",
            "-n",
            env_name,
            "conda-pack",
            "--ignore-editable-packages",
            "-o",
            str(env_archive),
        ]
    )


def save_docker_images(images_dir: Path) -> list[dict]:
    if not shutil.which("docker"):
        raise RuntimeError("docker is required to save offline images.")

    images_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    for image, filename in DOCKER_IMAGES:
        tar_path = images_dir / filename
        print(f"[docker] ensuring image {image}")
        run(["docker", "pull", image])
        print(f"[docker] saving {image} -> {tar_path}")
        run(["docker", "save", "-o", str(tar_path), image])
        records.append(
            {
                "image": image,
                "archive": str(tar_path.relative_to(ROOT)).replace("\\", "/"),
                "sha256": sha256(tar_path),
            }
        )
    return records


def write_text(path: Path, content: str, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.lstrip("\n"), encoding="utf-8")
    if executable:
        mode = path.stat().st_mode
        path.chmod(mode | 0o111)


def write_bundle_scripts(bundle_root: Path, *, env_name: str) -> None:
    install_dir = bundle_root / "install"
    config_dir = bundle_root / "config"
    env_dir = bundle_root / "envs" / env_name

    write_text(
        config_dir / "local-qwen.env",
        f"""
        LLM_PROFILE=local-qwen-gpu
        ENABLE_LLM=true
        LLM_PROVIDER=openai_compatible
        LOCAL_QWEN_MODEL_DIR=local_models/qwen3-1.7b
        LOCAL_QWEN_BASE_URL=http://127.0.0.1:11435/v1
        LOCAL_QWEN_PORT=11435
        LOCAL_QWEN_MODEL_NAME=qwen3:1.7b
        LOCAL_QWEN_DEVICE=cuda
        LOCAL_QWEN_DTYPE=float16
        A2A_ACT_AGENT_LLM=true
        ALGOLIB_ENABLE_LLM=true
        ALGOLIB_FALLBACK_LOCAL=true
        A2A_FORCE_ALGOLIB_FIRST=1
        """,
    )

    write_text(
        install_dir / "setup_env.sh",
        f"""
        #!/usr/bin/env bash
        set -euo pipefail

        SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
        ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
        ENV_DIR="$ROOT_DIR/envs/{env_name}"
        ARCHIVE="$ROOT_DIR/envs/{DEFAULT_ENV_ARCHIVE}"

        if [[ ! -f "$ARCHIVE" ]]; then
          echo "Missing Conda archive: $ARCHIVE" >&2
          exit 1
        fi

        rm -rf "$ENV_DIR"
        mkdir -p "$ENV_DIR"
        tar -xzf "$ARCHIVE" -C "$ENV_DIR"
        if [[ -x "$ENV_DIR/bin/conda-unpack" ]]; then
          "$ENV_DIR/bin/conda-unpack"
        fi

        REPO_DIR="$ROOT_DIR/repo"
        "$ENV_DIR/bin/python" - "$REPO_DIR" <<'PY'
import pathlib
import site
import sys

repo_dir = pathlib.Path(sys.argv[1]).resolve()
site_packages = pathlib.Path(site.getsitepackages()[0])
shim = site_packages / "000_a2a_bundle_sources.pth"
shim.write_text(
    str(repo_dir / "commander")
    + "\n"
    + str(repo_dir / "amos-platform" / "src")
    + "\n",
    encoding="utf-8",
)
print("[ok] Python source path shim written: " + str(shim))
PY
        echo "[ok] Conda environment unpacked at $ENV_DIR"
        """,
        executable=True,
    )

    write_text(
        install_dir / "load_images.sh",
        """
        #!/usr/bin/env bash
        set -euo pipefail

        SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
        IMAGES_DIR="$ROOT_DIR/docker-images"

        docker load -i "$IMAGES_DIR/nacos_nacos-server_v2.4.3.tar"
        docker load -i "$IMAGES_DIR/kennethreitz_httpbin_latest.tar"
        echo "[ok] Docker images loaded"
        """,
        executable=True,
    )

    write_text(
        install_dir / "build.sh",
        f"""
        #!/usr/bin/env bash
        set -euo pipefail

        SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
        ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
        REPO_DIR="$ROOT_DIR/repo"
        ENV_DIR="$ROOT_DIR/envs/{env_name}"

        if [[ ! -x "$ENV_DIR/bin/python" ]]; then
          "$SCRIPT_DIR/setup_env.sh"
        fi

        "$ENV_DIR/bin/python" -m pip check
        "$ENV_DIR/bin/cmake" -S "$REPO_DIR/commander" -B "$REPO_DIR/commander/build" -G Ninja \\
          -DALGOLIB_BUILD_TESTS=ON -DALGOLIB_WITH_ONNXRUNTIME=OFF
        "$ENV_DIR/bin/cmake" --build "$REPO_DIR/commander/build" -j "${{A2A_BUILD_JOBS:-2}}"
        echo "[ok] Commander / AlgoLib built"
        """,
        executable=True,
    )

    write_text(
        install_dir / "run.sh",
        f"""
        #!/usr/bin/env bash
        set -euo pipefail

        SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
        ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
        REPO_DIR="$ROOT_DIR/repo"
        ENV_DIR="$ROOT_DIR/envs/{env_name}"
        CONFIG_FILE="$ROOT_DIR/config/local-qwen.env"

        if [[ ! -x "$ENV_DIR/bin/python" ]]; then
          "$SCRIPT_DIR/setup_env.sh"
        fi
        if [[ ! -x "$REPO_DIR/commander/build/algolib_server" ]]; then
          "$SCRIPT_DIR/build.sh"
        fi
        if ! docker image inspect nacos/nacos-server:v2.4.3 >/dev/null 2>&1 || \\
           ! docker image inspect kennethreitz/httpbin:latest >/dev/null 2>&1; then
          "$SCRIPT_DIR/load_images.sh"
        fi

        if [[ -f "$CONFIG_FILE" ]]; then
          set -a
          # shellcheck disable=SC1090
          source "$CONFIG_FILE"
          set +a
        fi
        export A2A_CONDA_PREFIX="$ENV_DIR"
        export LLM_PROFILE="${{LLM_PROFILE:-local-qwen-gpu}}"
        export LOCAL_QWEN_MODEL_DIR="${{LOCAL_QWEN_MODEL_DIR:-$ROOT_DIR/local_models/qwen3-1.7b}}"
        export LOCAL_QWEN_BASE_URL="${{LOCAL_QWEN_BASE_URL:-http://127.0.0.1:11435/v1}}"
        export LOCAL_QWEN_PORT="${{LOCAL_QWEN_PORT:-11435}}"
        export LOCAL_QWEN_MODEL_NAME="${{LOCAL_QWEN_MODEL_NAME:-qwen3:1.7b}}"
        export LOCAL_QWEN_DEVICE="${{LOCAL_QWEN_DEVICE:-cuda}}"
        export LOCAL_QWEN_DTYPE="${{LOCAL_QWEN_DTYPE:-float16}}"

        exec "$REPO_DIR/scripts/start.sh" --llm-profile local-qwen-gpu --require-llm
        """,
        executable=True,
    )

    write_text(
        install_dir / "README.md",
        f"""
        # Offline deployment

        1. Install Docker Desktop and enable WSL integration on the target machine.
        2. Unpack `envs/{DEFAULT_ENV_ARCHIVE}`:
           `bash install/setup_env.sh`
        3. Load Docker images:
           `bash install/load_images.sh`
        4. Build AlgoLib and Commander offline:
           `bash install/build.sh`
        5. Start the system with the local Qwen profile:
           `bash install/run.sh`

        The runtime profile uses:
        - `local_models/qwen3-1.7b`
        - local OpenAI-compatible Qwen server on `127.0.0.1:11435`
        - `LLM_PROFILE=local-qwen-gpu`

        No Azure key is required for this offline bundle.
        """,
    )


def write_manifest(bundle_root: Path, *, docker_records: list[dict], env_archive: Path) -> None:
    manifest_files: list[dict] = []
    for path in sorted(bundle_root.rglob("*")):
        if path.is_file():
            manifest_files.append(
                {
                    "path": str(path.relative_to(bundle_root)).replace("\\", "/"),
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256(path),
                }
            )
    manifest = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "root": str(ROOT),
        "bundle_root": str(bundle_root),
        "env_name": DEFAULT_ENV_NAME,
        "env_archive": str(env_archive.relative_to(bundle_root)).replace("\\", "/"),
        "docker_images": docker_records,
        "file_count": len(manifest_files),
        "files": manifest_files,
    }
    (bundle_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def make_tarball(bundle_root: Path) -> Path:
    tarball = bundle_root.with_suffix(".tar.gz")
    if tarball.exists():
        tarball.unlink()
    with tarfile.open(tarball, "w:gz") as tf:
        tf.add(bundle_root, arcname=bundle_root.name)
    return tarball


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--env-name", default=DEFAULT_ENV_NAME)
    parser.add_argument("--skip-docker-images", action="store_true")
    parser.add_argument("--skip-models", action="store_true")
    args = parser.parse_args(argv)

    bundle_root = args.output_dir.resolve()
    ensure_clean_output_dir(bundle_root)

    print("[1/4] Copying repository snapshot")
    copy_repo_snapshot(bundle_root / DEFAULT_REPO_NAME)

    if not args.skip_models:
        if not DEFAULT_QWEN_DIR.is_dir():
            raise RuntimeError(f"Missing local Qwen model directory: {DEFAULT_QWEN_DIR}")
        print("[2/4] Copying local Qwen model")
        target_model_dir = bundle_root / "local_models" / DEFAULT_QWEN_DIR.name
        target_model_dir.parent.mkdir(parents=True, exist_ok=True)
        if target_model_dir.exists():
            shutil.rmtree(target_model_dir)
        shutil.copytree(DEFAULT_QWEN_DIR, target_model_dir)
    else:
        print("[2/4] Skipping local model copy")

    print("[3/4] Packing Conda environment")
    env_archive = bundle_root / "envs" / DEFAULT_ENV_ARCHIVE
    pack_conda_env(args.env_name, env_archive)

    docker_records: list[dict] = []
    if not args.skip_docker_images:
        print("[4/4] Saving Docker images")
        docker_records = save_docker_images(bundle_root / "docker-images")
    else:
        print("[4/4] Skipping Docker image export")

    write_bundle_scripts(bundle_root, env_name=args.env_name)
    write_manifest(bundle_root, docker_records=docker_records, env_archive=env_archive)
    tarball = make_tarball(bundle_root)

    print()
    print(f"[done] Bundle directory: {bundle_root}")
    print(f"[done] Bundle tarball:   {tarball}")
    print(f"[done] Install entry:    {bundle_root / 'install' / 'README.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
