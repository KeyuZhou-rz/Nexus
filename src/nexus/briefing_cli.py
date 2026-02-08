from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

from .aggregation import run_aggregation
from .config import default_config
from .intelligence.briefing import briefing_payload, build_briefing
from .storage import load_tasks, save_tasks


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Nexus daily briefing JSON.")
    parser.add_argument("--window-days", type=int, default=7, help="Rolling window size.")
    parser.add_argument(
        "--aggregate",
        action="store_true",
        help="Run aggregation before generating briefing.",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Disable LLM; use rule-based fallback only.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output JSON path (default: data/briefing.json).",
    )
    args = parser.parse_args()

    if args.aggregate:
        result = run_aggregation(include_google=True)
        if result.errors:
            for err in result.errors:
                print(f"[aggregation error] {err}")
        save_tasks(result.tasks)

    config = default_config()
    tasks = load_tasks()

    now_local = datetime.now().astimezone()
    window_start = now_local
    window_end = now_local + timedelta(days=args.window_days)

    briefing = build_briefing(
        tasks,
        window_days=args.window_days,
        now=now_local,
        config=config,
        use_llm=not args.no_llm,
    )
    payload = briefing_payload(briefing, window_start, window_end, generated_at=now_local)

    output_path = (
        Path(args.output)
        if args.output
        else (config.data_dir / "briefing.json")
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Briefing written to: {output_path}")


if __name__ == "__main__":
    main()
