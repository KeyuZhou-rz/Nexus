from __future__ import annotations

import argparse
import json
from pathlib import Path

from .pipeline import run_mvp_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Run integrated Nexus MVP pipeline.")
    parser.add_argument("--skip-archive-sync", action="store_true", help="Skip Playwright archive sync step")
    parser.add_argument("--archive-timeout", type=int, default=420, help="Archive sync timeout seconds")
    parser.add_argument("--no-google", action="store_true", help="Disable Google aggregators")
    parser.add_argument("--no-llm", action="store_true", help="Disable LLM in briefing")
    parser.add_argument("--window-days", type=int, default=7, help="Briefing window in days")
    parser.add_argument("--briefing-output", type=str, default=None, help="Override briefing output path")
    parser.add_argument("--report-output", type=str, default=None, help="Override pipeline report output path")
    args = parser.parse_args()

    report = run_mvp_pipeline(
        run_archive_sync=not args.skip_archive_sync,
        archive_timeout=args.archive_timeout,
        include_google=not args.no_google,
        use_llm=not args.no_llm,
        window_days=args.window_days,
        briefing_output=Path(args.briefing_output) if args.briefing_output else None,
        report_output=Path(args.report_output) if args.report_output else None,
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
