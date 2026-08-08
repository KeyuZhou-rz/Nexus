from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexus.cli.sync import main


@pytest.mark.parametrize(
    ("receipt", "expected_code"),
    [
        ({"ok": True, "status": "succeeded"}, 0),
        ({"ok": False, "status": "failed"}, 1),
        ({"ok": False, "status": "busy"}, 2),
    ],
)
def test_sync_cli_emits_json_and_maps_status_to_exit_code(
    receipt,
    expected_code,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr("nexus.cli.sync.run_sync", lambda **_: receipt)

    assert main(["--no-google"]) == expected_code
    captured = capsys.readouterr()
    assert json.loads(captured.out) == receipt
    assert captured.err == ""


def test_sync_cli_forwards_explicit_execution_policy(tmp_path: Path, monkeypatch, capsys) -> None:
    calls = []
    monkeypatch.setattr(
        "nexus.cli.sync.run_sync",
        lambda **kwargs: calls.append(kwargs) or {"ok": True, "status": "succeeded"},
    )

    assert main([
        "--data-dir",
        str(tmp_path),
        "--include-archive",
        "--archive-timeout",
        "17",
        "--no-google",
    ]) == 0
    assert calls == [{
        "data_dir": tmp_path,
        "trigger": "cli",
        "include_archive": True,
        "include_google": False,
        "archive_timeout": 17,
    }]
    assert json.loads(capsys.readouterr().out)["status"] == "succeeded"


def test_sync_cli_converts_unhandled_service_error_to_json(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "nexus.cli.sync.run_sync",
        lambda **_: (_ for _ in ()).throw(RuntimeError("token=secret")),
    )

    assert main([]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "schema_version": "1.0",
        "ok": False,
        "status": "failed",
        "trigger": "cli",
        "error_type": "RuntimeError",
    }
    assert "secret" not in captured.out
    assert captured.err == ""


def test_sync_cli_reports_invalid_data_dir_as_json(tmp_path: Path, capsys) -> None:
    invalid_data_dir = tmp_path / "runtime-file"
    invalid_data_dir.write_text("not a directory", encoding="utf-8")

    assert main(["--data-dir", str(invalid_data_dir), "--no-google"]) == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == "failed"
    assert payload["error_type"] == "FileExistsError"
    assert captured.err == ""
