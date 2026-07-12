from __future__ import annotations

import json
from pathlib import Path

from nexus.models import FeedSource, Task


def test_storage_and_pipeline_use_runtime_data_dir(monkeypatch, tmp_path):
    data_dir = tmp_path / "runtime-data"
    monkeypatch.setenv("NEXUS_DATA_DIR", str(data_dir))

    from nexus.pipeline import run_mvp_pipeline
    from nexus.storage import load_feeds, load_tasks, save_feeds, save_tasks

    task = Task(id="local-1", title="Local task", due_at=None, source="test")
    feed = FeedSource(
        kind="brightspace_ical",
        name="disabled",
        url="file:///unused.ics",
        enabled=False,
    )
    save_tasks([task])
    save_feeds([feed])
    assert load_tasks()[0].id == "local-1"
    assert load_feeds()[0].name == "disabled"

    report = run_mvp_pipeline(
        run_archive_sync=False,
        include_google=False,
        use_llm=False,
    )

    assert (data_dir / "tasks.json").exists()
    assert (data_dir / "feeds.json").exists()
    assert (data_dir / "briefing.json").exists()
    assert (data_dir / "pipeline_report.json").exists()
    assert Path(report["report_path"]).parent == data_dir


def test_archive_sync_derives_every_store_from_runtime_data_dir(monkeypatch, tmp_path, capsys):
    data_dir = tmp_path / "runtime-data"
    monkeypatch.setenv("NEXUS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("NEXUS_BRIGHTSPACE_URL", "https://example.invalid")
    monkeypatch.setenv("NEXUS_ARCHIVE_DEBUG", "0")
    monkeypatch.setenv("NEXUS_BRIGHTSPACE_LOGIN_MODE", "manual")

    from nexus.archive_sync import __main__ as archive_main

    captured = {}

    async def fake_scraper(*args, **kwargs):
        captured["archive_root"] = kwargs["archive_root"]
        captured["debug_dir"] = kwargs["debug_dir"]
        return {
            "status": "success",
            "message": "ok",
            "tasks": [{"id": "a1", "title": "Archived", "source": "brightspace"}],
            "archives": [],
            "archive_failures": [{"attachment_url": "https://example.invalid/f"}],
        }

    def fake_queue(path, failures, **kwargs):
        captured["failure_queue"] = path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(failures), encoding="utf-8")
        return {"failure_count": len(failures)}

    def fake_post_ingest(**kwargs):
        captured["post_ingest"] = kwargs
        kwargs["db_dir"].mkdir(parents=True, exist_ok=True)
        kwargs["sqlite_path"].touch()
        return {"status": "success"}

    monkeypatch.setattr(archive_main, "run_scraper", fake_scraper)
    monkeypatch.setattr(archive_main, "update_failure_queue", fake_queue)
    monkeypatch.setattr(archive_main, "run_archive_post_ingest", fake_post_ingest)

    archive_main.main()
    json.loads(capsys.readouterr().out)

    assert captured["archive_root"] == data_dir / "archives"
    assert captured["debug_dir"] == data_dir / "debug" / "archive_sync"
    assert captured["failure_queue"] == data_dir / "archive_failures.json"
    assert captured["post_ingest"]["data_dir"] == data_dir
    assert captured["post_ingest"]["db_dir"] == data_dir / "chroma"
    assert captured["post_ingest"]["sqlite_path"] == data_dir / "nexus.db"
    assert (data_dir / "tasks.json").exists()
    assert (data_dir / "archive_failures.json").exists()
    assert (data_dir / "nexus.db").exists()
    assert (data_dir / "chroma").is_dir()
