"""Preview or apply legacy mastery evidence migration."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Classify legacy learning events and rebuild trusted mastery"
    )
    parser.add_argument("--sqlite-path", default="data/nexus.db")
    parser.add_argument(
        "--apply", action="store_true", help="Apply changes; default is a dry run"
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    from nexus.knowledge.maintenance import migrate_legacy_mastery

    result = migrate_legacy_mastery(Path(args.sqlite_path), apply=args.apply)
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        mode = "APPLY" if args.apply else "DRY RUN"
        print(f"Legacy mastery migration: {mode}")
        print(f"  candidates={result['candidates']}")
        print(
            f"  trusted={result['will_mark_trusted']}, "
            f"legacy={result['will_mark_legacy']}, "
            f"exposure={result['will_mark_exposure']}"
        )
        print(f"  Backup first: {result['backup_hint']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
