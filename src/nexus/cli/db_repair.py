from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Conservatively repair deterministic index drift")
    parser.add_argument("--sqlite-path", default="data/nexus.db")
    parser.add_argument("--db-dir", default="data/chroma")
    parser.add_argument("--apply", action="store_true", help="Apply safe repairs; default is dry-run")
    args = parser.parse_args()

    from nexus.knowledge.maintenance import repair_consistency

    report = repair_consistency(Path(args.sqlite_path), Path(args.db_dir), apply=args.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
