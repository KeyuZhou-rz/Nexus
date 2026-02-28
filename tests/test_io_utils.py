from __future__ import annotations

import json

from nexus.io_utils import atomic_write_json, atomic_write_text


def test_atomic_write_text_replaces_content(tmp_path):
    path = tmp_path / "sample.txt"
    atomic_write_text(path, "hello")
    assert path.read_text(encoding="utf-8") == "hello"

    atomic_write_text(path, "world")
    assert path.read_text(encoding="utf-8") == "world"


def test_atomic_write_json_writes_valid_json(tmp_path):
    path = tmp_path / "payload.json"
    payload = {"a": 1, "b": [1, 2, 3]}
    atomic_write_json(path, payload)
    assert json.loads(path.read_text(encoding="utf-8")) == payload
