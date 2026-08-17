"""将 MOTR 训练终端日志解析为 runs/motr_kalman/training_log.csv。"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

BATCH_RE = re.compile(r"epoch\s+(\d+)\s+batch\s+(\d+)\s+loss=([\d.]+)")
EPOCH_RE = re.compile(
    r"Epoch\s+(\d+)/(\d+)\s+train_loss=([\d.]+)\s+val_assoc=([\d.]+)\s+val_kalman=([\d.]+)"
)
POS_PAIRS_RE = re.compile(r"positive pairs:\s+(\d+)")


def parse_log(text: str) -> tuple[list[dict], dict[str, str]]:
    rows: list[dict] = []
    meta: dict[str, str] = {}

    for line in text.splitlines():
        line = line.strip()
        if line.startswith("Train:"):
            meta["train_root"] = line.split(":", 1)[1].strip()
        elif line.startswith("Val:"):
            meta["val_root"] = line.split(":", 1)[1].strip()
        elif line.startswith("Device:"):
            meta["device"] = line.split(":", 1)[1].strip()
        elif m := POS_PAIRS_RE.search(line):
            key = "train_positive_pairs" if "train_positive_pairs" not in meta else "val_positive_pairs"
            meta[key] = m.group(1)
        elif m := BATCH_RE.search(line):
            rows.append(
                {
                    "record_type": "batch",
                    "epoch": int(m.group(1)),
                    "batch": int(m.group(2)),
                    "loss": float(m.group(3)),
                    "train_loss": "",
                    "val_assoc": "",
                    "val_kalman": "",
                    "saved_best": "",
                }
            )
        elif m := EPOCH_RE.search(line):
            saved = "1" if "[saved best]" in line else "0"
            rows.append(
                {
                    "record_type": "epoch",
                    "epoch": int(m.group(1)),
                    "batch": "",
                    "loss": "",
                    "train_loss": float(m.group(3)),
                    "val_assoc": float(m.group(4)),
                    "val_kalman": float(m.group(5)),
                    "saved_best": saved,
                }
            )
        elif line.startswith("[DONE] checkpoint"):
            meta["checkpoint"] = line.split("->", 1)[1].strip()

    return rows, meta


def main() -> int:
    parser = argparse.ArgumentParser(description="Parse MOTR training terminal log to CSV")
    parser.add_argument(
        "--log",
        type=Path,
        default=Path(
            r"C:\Users\33840\.cursor\projects\d-a2a-project-A2A-main\terminals\408052.txt"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("runs/motr_kalman/training_log.csv"),
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    log_path = args.log if args.log.is_absolute() else root / args.log
    out_path = args.output if args.output.is_absolute() else root / args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)

    text = log_path.read_text(encoding="utf-8", errors="replace")
    rows, meta = parse_log(text)

    fieldnames = [
        "record_type",
        "epoch",
        "batch",
        "loss",
        "train_loss",
        "val_assoc",
        "val_kalman",
        "saved_best",
    ]
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    meta_path = out_path.with_name("training_meta.txt")
    meta_lines = [f"{k}={v}" for k, v in meta.items()]
    meta_path.write_text("\n".join(meta_lines) + "\n", encoding="utf-8")

    print(f"[OK] {len(rows)} rows -> {out_path}")
    print(f"[OK] meta -> {meta_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
