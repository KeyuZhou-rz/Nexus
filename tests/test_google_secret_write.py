from __future__ import annotations

import stat

from nexus.core.aggregators.google import _atomic_write_secret


def test_atomic_secret_write_replaces_and_restricts_permissions(tmp_path):
    path = tmp_path / "google_token.json"
    path.write_text("old", encoding="utf-8")
    path.chmod(0o644)
    _atomic_write_secret(path, '{"access_token":"new"}')
    assert path.read_text(encoding="utf-8") == '{"access_token":"new"}'
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert list(tmp_path.glob(".*google_token.json.*")) == []
