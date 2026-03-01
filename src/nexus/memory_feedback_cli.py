from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from .memory_update import apply_feedback
from .state_store import load_state, save_state


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply human feedback to weak-point memory")
    parser.add_argument("--topic", required=True)
    parser.add_argument("--action", required=True, choices=["accept", "reject"])
    parser.add_argument("--state-path", default="data/state.json")
    args = parser.parse_args()

    state_path = Path(args.state_path)
    state = load_state(state_path)
    now_iso = datetime.now().astimezone().isoformat()
    state = apply_feedback(state, args.topic, args.action, now_iso)
    save_state(state_path, state)

    print(f"Updated topic '{args.topic}' with action '{args.action}'.")
    print(f"Active weak points: {len(state.weak_points)}")


if __name__ == "__main__":
    main()
