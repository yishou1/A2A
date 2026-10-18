"""Select the latest stable version of each bundled algorithm/backend pair."""
from __future__ import annotations

import re
import sys
from pathlib import Path


def _field(text: str, name: str, *, nested: bool = False) -> str:
    indent = r"\s*" if nested else ""
    match = re.search(rf"^{indent}{re.escape(name)}:\s*([^\s#]+)", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def algorithm_card_rows(root: Path) -> list[tuple[str, str, str, str, str]]:
    selected: dict[tuple[str, str], tuple[tuple[int, ...], tuple[str, str, str, str, str]]] = {}
    for card in sorted(root.glob("*/*/algorithm_card.yaml")):
        text = card.read_text(encoding="utf-8")
        algorithm_id = _field(text, "algorithm_id") or card.parents[1].name
        version = _field(text, "version")
        if not re.fullmatch(r"\d+\.\d+\.\d+", version):
            continue
        backend = _field(text, "backend_type") or _field(text, "backend_type", nested=True)
        if not backend:
            continue
        key = (algorithm_id, backend)
        version_key = tuple(map(int, version.split(".")))
        row = (card.parents[1].name, algorithm_id, version, backend,
               _field(text, "health_endpoint", nested=True))
        if key not in selected or version_key > selected[key][0]:
            selected[key] = (version_key, row)
    return [selected[key][1] for key in sorted(selected)]


if __name__ == "__main__":
    for row in algorithm_card_rows(Path(sys.argv[1])):
        print("\t".join(row))
