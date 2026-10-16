"""独立算法 CLI：python -m algorithms <algorithm_id> --inputs inputs.json"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one decoupled algorithm standalone")
    parser.add_argument(
        "algorithm_id",
        nargs="?",
        default=None,
        help="e.g. battlefield_rtdetr_detector",
    )
    parser.add_argument("--inputs", default=None, help="JSON file with algorithm inputs")
    parser.add_argument("--params", default=None, help="Optional JSON params file")
    parser.add_argument("--output", default=None, help="Write outputs JSON to this path")
    parser.add_argument(
        "--tier",
        default=None,
        help="Compute tier: low|mid|high (or small|medium|large). Merges config/profiles/*.yaml",
    )
    parser.add_argument(
        "--mock",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Force mock/real (default: TIA_USE_MOCK env, else mock)",
    )
    parser.add_argument(
        "--validate",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Validate I/O against algorithm contracts (default: on)",
    )
    parser.add_argument(
        "--list-contracts",
        action="store_true",
        help="Print contract summary for all algorithms and exit",
    )
    parser.add_argument(
        "--list-tiers",
        action="store_true",
        help="Print low/mid/high compute tiers and exit",
    )
    args = parser.parse_args(argv)

    from algorithms import (
        ALGORITHM_IDS,
        ALGORITHM_MATURITY,
        contract_summary,
        invoke,
        list_tiers,
        tier_summary,
    )

    if args.list_tiers:
        print(json.dumps(list_tiers(), ensure_ascii=False, indent=2))
        return 0

    if args.list_contracts:
        print(json.dumps(contract_summary(), ensure_ascii=False, indent=2))
        return 0

    if not args.algorithm_id or not args.inputs:
        parser.error("algorithm_id and --inputs are required (or use --list-contracts / --list-tiers)")

    if args.algorithm_id not in ALGORITHM_IDS:
        print(f"unknown algorithm_id: {args.algorithm_id}", file=sys.stderr)
        print("available:", ", ".join(ALGORITHM_IDS), file=sys.stderr)
        return 2

    inputs = json.loads(Path(args.inputs).read_text(encoding="utf-8-sig"))
    params: dict = {}
    if args.params:
        params = json.loads(Path(args.params).read_text(encoding="utf-8-sig"))

    if args.mock is None:
        use_mock = os.environ.get("TIA_USE_MOCK", "1") not in ("0", "false", "False")
    else:
        use_mock = bool(args.mock)

    outputs = invoke(
        args.algorithm_id,
        inputs,
        use_mock=use_mock,
        params=params,
        config=params,
        validate=bool(args.validate),
        tier=args.tier,
    )
    payload = {
        "ok": True,
        "algorithm_id": args.algorithm_id,
        "maturity": ALGORITHM_MATURITY.get(args.algorithm_id, "unknown"),
        "use_mock": use_mock,
        "validated": bool(args.validate),
        "tier": tier_summary(params, tier=args.tier),
        "outputs": outputs,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
