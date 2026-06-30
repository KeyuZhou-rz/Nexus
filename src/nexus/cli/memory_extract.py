from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from ..conversation_store import load_messages
from ..memory_extractor import extract_weak_points
from ..memory_writer import record_weak_point
from ..knowledge.sqlite_store import SQLiteStore
from ..knowledge.concept_resolver import ConceptResolver
from ..knowledge.user_profile import UserProfileManager
from ..knowledge.temporal_memory import TemporalMemory
from ..knowledge.store import ChromaKnowledgeStore


def _candidate_id(session_id: str, topic: str, idx: int) -> str:
    key = topic.replace(" ", "_")[:40]
    return f"mem:{session_id}:{key}:{idx}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract weak points from conversation logs")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--from-index", type=int, default=0)
    parser.add_argument("--conversations-dir", default="data/conversations")
    parser.add_argument("--db-path", default="data/nexus.db")
    parser.add_argument("--db-dir", default="data/chroma")
    parser.add_argument("--collection", default="nexus_memory_evidence")
    args = parser.parse_args()

    now_iso = datetime.now().astimezone().isoformat()
    messages = load_messages(Path(args.conversations_dir), args.session_id, from_index=args.from_index)
    if not messages:
        print("No messages found for extraction.")
        return

    candidates = extract_weak_points(messages)

    # Unified SQLite write via memory_writer
    db_path = Path(args.db_path)
    store = SQLiteStore(db_path)
    resolver = ConceptResolver(store)
    profile_mgr = UserProfileManager(store)
    temporal = TemporalMemory(store)

    resolved_count = 0
    for item in candidates:
        result = record_weak_point(
            resolver, profile_mgr, temporal,
            item.topic, course_id="conversation",
            session_id=args.session_id,
            context={"source": "chat_extraction", "confidence": item.confidence},
        )
        if result["resolved"]:
            resolved_count += 1

    if candidates:
        try:
            chroma_store = ChromaKnowledgeStore(Path(args.db_dir), collection_name=args.collection)
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
            chroma_store.upsert_chunks(ids, texts, metas)
        except Exception as exc:
            print(f"Warning: failed to upsert memory evidence to chroma: {exc}")

    profile = profile_mgr.get()
    active_weak = len(profile.get("weak_points", []))
    print(f"Processed messages: {len(messages)}")
    print(f"Extracted weak points: {len(candidates)}")
    print(f"Resolved to concepts: {resolved_count}/{len(candidates)}")
    print(f"Active weak points in profile: {active_weak}")


if __name__ == "__main__":
    main()
