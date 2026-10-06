from __future__ import annotations

import json
import os
from pathlib import Path

from nexus.core.aggregation import AggregationResult, run_aggregation
from nexus.core.aggregators.google import GoogleAggregator
from nexus.core.config import AppConfig
from nexus.core.models import FeedSource, Task


def test_storage_and_pipeline_use_runtime_data_dir(monkeypatch, tmp_path):
    data_dir = tmp_path / "runtime-data"
    monkeypatch.setenv("NEXUS_DATA_DIR", str(data_dir))

    from nexus.pipeline import run_mvp_pipeline
    from nexus.core.storage import load_feeds, load_tasks, save_feeds, save_tasks

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


def test_archive_subprocess_explicit_data_dir_overrides_inherited_env(
    monkeypatch,
    tmp_path,
):
    inherited_dir = tmp_path / "inherited"
    explicit_dir = tmp_path / "explicit"
    monkeypatch.setenv("NEXUS_DATA_DIR", str(inherited_dir))
    captured = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return type("Completed", (), {"stdout": "{}", "stderr": "", "returncode": 0})()

    monkeypatch.setattr("nexus.archive_sync.subprocess.run", fake_run)

    from nexus.archive_sync import run_archive_sync_subprocess

    assert run_archive_sync_subprocess(data_dir=explicit_dir) == {}
    assert captured["env"]["NEXUS_DATA_DIR"] == str(explicit_dir)
    assert captured["env"]["PYTHONPATH"]
    assert all(
        captured["env"].get(key) == value
        for key, value in os.environ.items()
        if key not in {"NEXUS_DATA_DIR", "PYTHONPATH"}
    )

    captured.clear()
    assert run_archive_sync_subprocess() == {}
    assert captured["env"]["NEXUS_DATA_DIR"] == str(inherited_dir)


def test_pipeline_passes_effective_data_dir_to_archive_subprocess(monkeypatch, tmp_path):
    data_dir = tmp_path / "explicit"
    inherited_dir = tmp_path / "inherited"
    captured = {}
    monkeypatch.setenv("NEXUS_DATA_DIR", str(inherited_dir))
    monkeypatch.setenv("NEXUS_BRIGHTSPACE_URL", "https://example.invalid")
    monkeypatch.setenv("NEXUS_BRIGHTSPACE_LOGIN_MODE", "manual")
    monkeypatch.setattr(
        "nexus.pipeline.default_config",
        lambda: AppConfig(data_dir=inherited_dir),
    )

    def fake_archive_sync(*, timeout, data_dir):
        captured.update(timeout=timeout, data_dir=data_dir)
        return {"status": "success", "tasks": [], "archives": [], "archive_failures": []}

    monkeypatch.setattr("nexus.pipeline.run_archive_sync_subprocess", fake_archive_sync)
    monkeypatch.setattr(
        "nexus.core.collect.run_aggregation",
        lambda **kwargs: AggregationResult(tasks=[], errors=[]),
    )
    monkeypatch.setattr("nexus.pipeline.load_tasks", lambda path: [])

    from nexus.pipeline import run_mvp_pipeline

    report = run_mvp_pipeline(
        data_dir=data_dir,
        run_archive_sync=True,
        archive_timeout=37,
        include_google=False,
        use_llm=False,
    )

    assert report["steps"][0]["status"] == "succeeded"
    assert captured == {"timeout": 37, "data_dir": data_dir}


def test_aggregation_google_config_is_isolated_between_runtimes(monkeypatch, tmp_path):
    runtime_a = tmp_path / "runtime-a"
    runtime_b = tmp_path / "runtime-b"
    captured = []

    def make_config(data_dir: Path, course: str) -> AppConfig:
        data_dir.mkdir()
        credentials_path = data_dir / "client.json"
        token_path = data_dir / "token.json"
        credentials_path.write_text("{}", encoding="utf-8")
        token_path.write_text("{}", encoding="utf-8")
        (data_dir / "course_aliases.json").write_text(
            json.dumps([{"course": course, "aliases": [course.lower()]}]),
            encoding="utf-8",
        )
        return AppConfig(
            data_dir=data_dir,
            google_credentials_path=credentials_path,
            google_token_path=token_path,
        )

    config_a = make_config(runtime_a, "COURSE-A")
    config_b = make_config(runtime_b, "COURSE-B")

    def fake_fetch(aggregator):
        captured.append(
            (
                aggregator.credentials_path,
                aggregator.token_path,
                aggregator.course_aliases,
            )
        )
        return []

    monkeypatch.setattr(GoogleAggregator, "fetch_tasks", fake_fetch)
    monkeypatch.setattr(
        "nexus.core.aggregators.google.default_config",
        lambda: (_ for _ in ()).throw(AssertionError("ambient config was read")),
    )

    for config in (config_a, config_b):
        run_aggregation(
            include_google=True,
            include_canvas=False,
            data_dir=config.data_dir,
            config=config,
        )

    assert captured == [
        (
            config_a.google_credentials_path,
            config_a.google_token_path,
            [{"course": "COURSE-A", "aliases": ["course-a"]}],
        ),
        (
            config_b.google_credentials_path,
            config_b.google_token_path,
            [{"course": "COURSE-B", "aliases": ["course-b"]}],
        ),
    ]
