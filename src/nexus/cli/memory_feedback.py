from __future__ import annotations

import argparse
from pathlib import Path

from ..memory_writer import apply_feedback_unified
from ..knowledge.sqlite_store import SQLiteStore
from ..knowledge.concept_resolver import ConceptResolver
from ..knowledge.user_profile import UserProfileManager
from ..knowledge.temporal_memory import TemporalMemory


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply human feedback to weak-point memory")
    parser.add_argument("--topic", required=True)
    parser.add_argument("--action", required=True, choices=["accept", "reject"])
    parser.add_argument("--db-path", default="data/nexus.db")
    parser.add_argument(
        "--course-id",
        default="conversation",
        help="Course ID used for concept resolution binding (default: conversation)",
    )
    args = parser.parse_args()

    db_path = Path(args.db_path)
    store = SQLiteStore(db_path)
    resolver = ConceptResolver(store)
    temporal = TemporalMemory(store)
    profile_mgr = UserProfileManager(store, temporal_memory=temporal, concept_resolver=resolver)

    result = apply_feedback_unified(
        resolver, profile_mgr, temporal,
        args.topic, course_id=args.course_id,
        action=args.action, session_id=None,
    )

    profile = profile_mgr.get()
    active_weak = len(profile.get("weak_points", []))
    mastered = len(profile.get("mastered", []))

    print(f"Action '{args.action}' on topic '{args.topic}'")
    print(f"  Resolved: {result['resolved']} (concept_id={result.get('concept_id')})")
    print(f"Active weak points: {active_weak}")
    print(f"Mastered concepts: {mastered}")


if __name__ == "__main__":
    main()
