from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from integrated_system.app import build_integrated_demo_app


def main() -> None:
    port = int(os.environ.get("INTEGRATED_DEMO_PORT", "8031"))
    app = build_integrated_demo_app()
    uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
