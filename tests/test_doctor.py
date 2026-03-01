from __future__ import annotations

from pathlib import Path

from nexus.config import AppConfig
from nexus.doctor import run_doctor


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
