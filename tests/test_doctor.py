from __future__ import annotations

from pathlib import Path

from nexus.config import AppConfig
from nexus.doctor import run_doctor
from nexus.config import default_config


def test_doctor_reports_missing_feeds_warning(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg = AppConfig(
        data_dir=data_dir,
        feeds_path=data_dir / "feeds.json",
        google_credentials_path=data_dir / "google_client_secret.json",
        google_token_path=data_dir / "google_token.json",
    )

    report = run_doctor(config=cfg, include_google=False)
    assert report.ok is True
    assert any("No enabled Brightspace feeds" in msg for msg in report.warnings)


def test_doctor_details_include_paths(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "tasks.json").write_text("[]", encoding="utf-8")
    cfg = AppConfig(
        data_dir=data_dir,
        feeds_path=data_dir / "feeds.json",
        google_credentials_path=data_dir / "google_client_secret.json",
        google_token_path=data_dir / "google_token.json",
    )

    report = run_doctor(config=cfg, include_google=False)
    assert report.details["tasks_exists"] is True
    assert report.details["tasks_size"] >= 2


def test_default_config_honors_data_dir_env(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXUS_DATA_DIR", str(tmp_path / "custom-data"))
    cfg = default_config()
    assert cfg.data_dir == tmp_path / "custom-data"
    assert cfg.feeds_path == tmp_path / "custom-data" / "feeds.json"


def test_doctor_reports_storage_status(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    cfg = AppConfig(data_dir=data_dir, feeds_path=data_dir / "feeds.json")
    report = run_doctor(config=cfg, include_google=False)
    assert report.details["sqlite_status"] == "missing"
    assert report.details["chroma_status"] == "missing"
    assert any("SQLite database not initialized" in msg for msg in report.warnings)


def test_doctor_detects_sqlite_corruption(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "nexus.db").write_bytes(b"not sqlite")
    cfg = AppConfig(data_dir=data_dir, feeds_path=data_dir / "feeds.json")
    report = run_doctor(config=cfg, include_google=False)
    assert report.ok is False
    assert report.details["sqlite_status"] == "unreadable"
