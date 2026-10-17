from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path_factory, monkeypatch):
    """Keep tests that rely on default_config() out of the real ``data/``."""
    monkeypatch.setenv("NEXUS_DATA_DIR", str(tmp_path_factory.mktemp("nexus-data")))
