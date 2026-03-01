from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from .conversation_store import load_messages
from .memory_extractor import extract_weak_points
from .memory_update import apply_confidence_decay, merge_candidates_into_state
from .state_store import load_state, save_state
from .knowledge.store import ChromaKnowledgeStore


def _candidate_id(session_id: str, topic: str, idx: int) -> str:
    key = topic.replace(" ", "_")[:40]
    return f"mem:{session_id}:{key}:{idx}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract weak points from conversation logs")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--from-index", type=int, default=0)
    parser.add_argument("--conversations-dir", default="data/conversations")
    parser.add_argument("--state-path", default="data/state.json")
    parser.add_argument("--db-dir", default="data/chroma")
    parser.add_argument("--collection", default="nexus_memory_evidence")
    parser.add_argument("--daily-decay", type=float, default=0.98)
    args = parser.parse_args()

    now_iso = datetime.now().astimezone().isoformat()
    messages = load_messages(Path(args.conversations_dir), args.session_id, from_index=args.from_index)
    if not messages:
        print("No messages found for extraction.")
        return

    candidates = extract_weak_points(messages)
    state_path = Path(args.state_path)
    state = load_state(state_path)
    state = apply_confidence_decay(state, now_iso=now_iso, daily_decay=args.daily_decay)
    state = merge_candidates_into_state(state, candidates, now_iso=now_iso)
    save_state(state_path, state)

    if candidates:
        try:
            store = ChromaKnowledgeStore(Path(args.db_dir), collection_name=args.collection)
            ids: list[str] = []
            texts: list[str] = []
            metas: list[dict[str, str]] = []
            for idx, item in enumerate(candidates):
                ids.append(_candidate_id(args.session_id, item.topic, idx))
                texts.append(item.evidence)
                metas.append(
                    {
                        "topic": item.topic,
                        "session_id": args.session_id,
                        "timestamp": now_iso,
                        "confidence": f"{item.confidence:.3f}",
                        "msg_ids": ",".join(item.evidence_msg_ids),
                        "doc_type": "conversation_memory",
                        "course_id": "conversation",
                        "file_name": f"session:{args.session_id}",
                    }
                )
            store.upsert_chunks(ids, texts, metas)
        except Exception as exc:
            print(f"Warning: failed to upsert memory evidence to chroma: {exc}")

    print(f"Processed messages: {len(messages)}")
    print(f"Extracted weak points: {len(candidates)}")
    print(f"Active weak points in state: {len(state.weak_points)}")


if __name__ == "__main__":
    main()
