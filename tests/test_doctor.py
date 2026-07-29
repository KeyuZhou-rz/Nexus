from __future__ import annotations

from pathlib import Path
import sys
from types import ModuleType

from nexus.config import AppConfig
from nexus.doctor import run_doctor
from nexus.config import default_config
from nexus.knowledge.sqlite_store import SQLiteStore


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


def test_doctor_does_not_warn_about_read_only_foreign_keys(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    SQLiteStore(data_dir / "nexus.db")

    report = run_doctor(config=AppConfig(data_dir=data_dir, feeds_path=data_dir / "feeds.json"), include_google=False)

    sqlite = report.components["sqlite"]
    assert sqlite.available is True
    assert sqlite.details["foreign_keys"] == "0"
    assert not any("Foreign keys not enforced" in warning for warning in report.warnings)


def test_doctor_reports_playwright_browser_install_command(tmp_path, monkeypatch):
    class BrokenChromium:
        def launch(self, **_kwargs):
            raise RuntimeError("browser binary missing")

    class FakePlaywright:
        chromium = BrokenChromium()

    class FakeContext:
        def __enter__(self):
            return FakePlaywright()

        def __exit__(self, *_args):
            return False

    playwright = ModuleType("playwright")
    sync_api = ModuleType("playwright.sync_api")
    sync_api.sync_playwright = FakeContext
    monkeypatch.setitem(sys.modules, "playwright", playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)

    import nexus.doctor as doctor

    original_available = doctor._module_available
    monkeypatch.setattr(
        doctor,
        "_module_available",
        lambda name: True if name == "playwright" else original_available(name),
    )
    report = run_doctor(
        config=AppConfig(
            data_dir=tmp_path / "data",
            brightspace_url="https://brightspace.example.test",
            brightspace_username="student",
            brightspace_password="secret",
        ),
        include_google=False,
    )

    assert report.components["archive"].errors == [
        "Playwright Chromium browser not installed. Run: python -m playwright install chromium"
    ]


def test_doctor_detects_sqlite_corruption(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "nexus.db").write_bytes(b"not sqlite")
    cfg = AppConfig(data_dir=data_dir, feeds_path=data_dir / "feeds.json")
    report = run_doctor(config=cfg, include_google=False)
    assert report.ok is False
    assert report.details["sqlite_status"] == "unreadable"


def test_doctor_reports_secret_permissions_without_reading_contents(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    env_path = tmp_path / ".env"
    env_path.write_text("NEXUS_LLM_API_KEY=do-not-print\n", encoding="utf-8")
    token_path = data_dir / "google_token.json"
    token_path.write_text('{"access_token":"do-not-print"}', encoding="utf-8")
    legacy_key = data_dir / "QWEN_API_KEY.json"
    legacy_key.write_text('{"QWEN_API_KEY":"do-not-print"}', encoding="utf-8")
    env_path.chmod(0o644)
    token_path.chmod(0o644)
    legacy_key.chmod(0o644)
    cfg = AppConfig(
        data_dir=data_dir,
        feeds_path=data_dir / "feeds.json",
        google_token_path=token_path,
        google_credentials_path=None,
    )
    # Doctor resolves .env from the project root, so assert the data-scoped
    # checks here and keep the test independent of the developer's .env.
    report = run_doctor(config=cfg, include_google=False)
    assert any("google_token" in warning and "chmod 600" in warning for warning in report.warnings)
    assert any("legacy_llm_key" in warning and "chmod 600" in warning for warning in report.warnings)
    assert all("do-not-print" not in warning for warning in report.warnings)


def test_doctor_records_owner_only_secret_permissions(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    key_path = data_dir / "QWEN_API_KEY.json"
    key_path.write_text("{}", encoding="utf-8")
    key_path.chmod(0o600)
    report = run_doctor(config=AppConfig(data_dir=data_dir), include_google=False)
    assert report.details["secret_permissions"]["legacy_llm_key"] == "0600"
    assert not any("legacy_llm_key" in warning for warning in report.warnings)
