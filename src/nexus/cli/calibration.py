"""Run offline assessment calibration fixtures."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..knowledge.calibration import CalibrationDataError, evaluate, load_cases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate an offline assessment calibration fixture")
    parser.add_argument("--fixture", type=Path, required=True, help="JSON calibration fixture")
    parser.add_argument("--min-accuracy", type=float, default=0.8)
    parser.add_argument("--max-ece", type=float, default=0.2)
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON output")
    args = parser.parse_args(argv)

    try:
        report = evaluate(load_cases(args.fixture))
    except CalibrationDataError as exc:
        payload = {"ok": False, "error": str(exc)}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2

    payload = {
        "ok": report.passes(min_accuracy=args.min_accuracy, max_ece=args.max_ece),
        "thresholds": {
            "min_accuracy": args.min_accuracy,
            "max_ece": args.max_ece,
        },
        "report": report.as_dict(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1
