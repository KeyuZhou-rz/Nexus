from __future__ import annotations

from nexus.conversation_store import append_message, load_messages


def test_append_and_load_messages(tmp_path):
    conv_dir = tmp_path / "conversations"
    session_id = "sess_1"

    m1 = append_message(conv_dir, session_id, role="user", content="I don't understand op-amp feedback")
    m2 = append_message(conv_dir, session_id, role="assistant", content="Let's break it down")

    loaded = load_messages(conv_dir, session_id)
    assert len(loaded) == 2
    assert loaded[0]["id"] == m1
    assert loaded[1]["id"] == m2


def test_load_messages_from_index(tmp_path):
    conv_dir = tmp_path / "conversations"
    session_id = "sess_2"
    append_message(conv_dir, session_id, role="user", content="A")
    append_message(conv_dir, session_id, role="user", content="B")

    loaded = load_messages(conv_dir, session_id, from_index=1)
    assert len(loaded) == 1
    assert loaded[0]["content"] == "B"
