from __future__ import annotations

import argparse
import json

from .doctor import run_doctor


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Nexus environment doctor checks")
    parser.add_argument("--no-google", action="store_true", help="Skip Google dependency checks")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero on warnings or failures")
    args = parser.parse_args()

    report = run_doctor(include_google=not args.no_google)
    payload = {
        "ok": report.ok,
        "failures": report.failures,
        "warnings": report.warnings,
        "details": report.details,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if report.failures:
        raise SystemExit(2)
    if args.strict and report.warnings:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
