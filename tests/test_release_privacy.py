from __future__ import annotations

from pathlib import Path
import re
import subprocess

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


PROJECT_ROOT = Path(__file__).parents[1]
ALLOWED_DATA_FILES = {"data/feeds.example.json"}


def test_source_release_tracks_no_runtime_or_credential_artifacts() -> None:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    tracked = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    violations = sorted(
        path
        for path in tracked
        if (
            (path.startswith("data/") and path not in ALLOWED_DATA_FILES)
            or "/__pycache__/" in path
            or path.endswith(".pyc")
            or path.endswith("/.DS_Store")
            or path == ".DS_Store"
            or "client_secret" in path.casefold()
            or path.casefold().endswith("token.json")
            or path == ".env"
        )
    )
    assert violations == [], "release contains local/private artifacts: " + ", ".join(violations)


def test_streamlit_usage_stats_are_disabled() -> None:
    config = tomllib.loads((PROJECT_ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert config["browser"]["gatherUsageStats"] is False


def test_release_version_is_consistent_across_metadata_and_beta_docs() -> None:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = project["project"]["version"]
    changelog = (PROJECT_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    beta = (PROJECT_ROOT / "docs" / "PRIVATE_BETA.md").read_text(encoding="utf-8")

    assert f"## {version} -" in changelog
    assert f"nexus_dashboard-{version}-py3-none-any.whl" in beta


def test_release_workflow_runs_packaged_sync_on_supported_platforms() -> None:
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "tests.yml").read_text(
        encoding="utf-8"
    )

    assert "runs-on: macos-latest" in workflow
    assert "runs-on: windows-latest" in workflow
    assert "python -m pip install dist/*.whl" in workflow
    assert "python -m pip install --no-deps dist/*.whl" not in workflow
    assert "nexus sync --data-dir /tmp/nexus-wheel-sync --no-google" in workflow
    assert "test-concurrency:" in workflow
    assert "make soak SOAK_ITERATIONS=100" in workflow


def test_private_beta_quickstart_uses_one_explicit_absolute_runtime() -> None:
    beta = (PROJECT_ROOT / "docs" / "PRIVATE_BETA.md").read_text(encoding="utf-8")

    assert 'export NEXUS_DATA_DIR="$(pwd -P)/nexus-data"' in beta
    assert 'nexus init --data-dir "$NEXUS_DATA_DIR" --json' in beta
    assert "nexus init --data-dir data --json" not in beta
    assert "nexus dashboard" in beta
    assert "nexus db-backup" in beta
    assert "Both `nexus dashboard` and the default `nexus db-backup` paths inherit it" in beta


def test_local_release_check_runs_isolated_installed_wheel_smoke() -> None:
    makefile = (PROJECT_ROOT / "Makefile").read_text(encoding="utf-8")

    assert re.search(r"^wheel-smoke: package$", makefile, flags=re.MULTILINE)
    assert re.search(
        r"^release-check: test v1-acceptance release-smoke soak wheel-smoke$",
        makefile,
        flags=re.MULTILINE,
    )
    assert "PYTHONPATH= python tests/scripts/wheel_smoke.py" in makefile
    assert "--timeout-seconds $(WHEEL_SMOKE_TIMEOUT_SECONDS)" in makefile
