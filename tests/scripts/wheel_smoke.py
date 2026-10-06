"""Build-artifact smoke test that never imports Nexus from the checkout."""

from __future__ import annotations

import argparse
from email.parser import Parser
import json
import os
from pathlib import Path
import site
import subprocess
import sys
import tempfile
import time
from typing import Any
from zipfile import ZipFile

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_TYPES: dict[str, type[Any]] = {
    "tasks.json": list,
    "briefing.json": dict,
    "pipeline_report.json": dict,
    "sync_receipt.json": dict,
}


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    deadline: float,
) -> subprocess.CompletedProcess[str]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError(f"wheel smoke timed out before running: {' '.join(command)}")
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=remaining,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"wheel smoke timed out while running: {' '.join(command)}\n"
            f"stdout:\n{exc.stdout or ''}\nstderr:\n{exc.stderr or ''}"
        ) from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"wheel smoke command failed ({result.returncode}): {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _wheel_metadata(path: Path) -> dict[str, str]:
    with ZipFile(path) as archive:
        metadata_names = [
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_names) != 1:
            raise RuntimeError(
                f"expected one METADATA file in {path}, found {len(metadata_names)}"
            )
        message = Parser().parsestr(archive.read(metadata_names[0]).decode("utf-8"))
    return {"name": message.get("Name", ""), "version": message.get("Version", "")}


def _select_current_wheel(dist_dir: Path) -> tuple[Path, str]:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    expected_name = project["project"]["name"]
    expected_version = project["project"]["version"]
    matches = []
    for candidate in sorted(dist_dir.glob("nexus_dashboard-*.whl")):
        metadata = _wheel_metadata(candidate)
        if metadata == {"name": expected_name, "version": expected_version}:
            matches.append(candidate.resolve())
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one {expected_name} {expected_version} wheel in {dist_dir}, "
            f"found {len(matches)}"
        )
    return matches[0], expected_version


def _json_object(stdout: str, *, command: str) -> dict[str, Any]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{command} did not emit valid JSON: {stdout!r}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{command} emitted {type(payload).__name__}, expected object")
    return payload


def run_smoke(*, dist_dir: Path, timeout_seconds: int) -> dict[str, Any]:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    wheel, expected_version = _select_current_wheel(dist_dir.resolve())
    deadline = time.monotonic() + timeout_seconds

    with tempfile.TemporaryDirectory(prefix="nexus-wheel-smoke-") as temp:
        temp_root = Path(temp).resolve()
        venv_dir = temp_root / "venv"
        work_dir = temp_root / "work"
        data_dir = temp_root / "runtime"
        work_dir.mkdir()

        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        env.update(
            {
                "PYTHONNOUSERSITE": "1",
                "PIP_NO_INDEX": "1",
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "NEXUS_DATA_DIR": str(data_dir),
                "NEXUS_LLM_ENABLED": "0",
                "NEXUS_BRIGHTSPACE_URL": "",
                "NEXUS_BRIGHTSPACE_USERNAME": "",
                "NEXUS_BRIGHTSPACE_PASSWORD": "",
                "NEXUS_CANVAS_URL": "",
                "NEXUS_CANVAS_TOKEN": "",
                "HTTP_PROXY": "http://127.0.0.1:9",
                "HTTPS_PROXY": "http://127.0.0.1:9",
                "ALL_PROXY": "http://127.0.0.1:9",
                "NO_PROXY": "",
            }
        )

        _run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            cwd=work_dir,
            env=env,
            deadline=deadline,
        )
        if os.name == "nt":
            venv_python = venv_dir / "Scripts" / "python.exe"
            nexus_command = venv_dir / "Scripts" / "nexus.exe"
        else:
            venv_python = venv_dir / "bin" / "python"
            nexus_command = venv_dir / "bin" / "nexus"

        purelib_probe = _run(
            [
                str(venv_python),
                "-c",
                "import json, sysconfig; print(json.dumps(sysconfig.get_path('purelib')))",
            ],
            cwd=work_dir,
            env=env,
            deadline=deadline,
        )
        venv_purelib = Path(json.loads(purelib_probe.stdout))
        dependency_paths = sorted(
            {
                Path(item).resolve()
                for item in site.getsitepackages()
                if Path(item).is_dir()
            }
        )
        if not dependency_paths:
            raise RuntimeError("no existing site-packages directory is available for wheel dependencies")
        (venv_purelib / "_nexus_wheel_smoke_dependencies.pth").write_text(
            "".join(f"{path}\n" for path in dependency_paths),
            encoding="utf-8",
        )

        _run(
            [
                str(venv_python),
                "-m",
                "pip",
                "install",
                "--force-reinstall",
                "--no-deps",
                "--no-index",
                str(wheel),
            ],
            cwd=work_dir,
            env=env,
            deadline=deadline,
        )
        _run(
            [str(venv_python), "-m", "pip", "check"],
            cwd=work_dir,
            env=env,
            deadline=deadline,
        )

        import_probe = _run(
            [
                str(venv_python),
                "-c",
                (
                    "import importlib.metadata, json, nexus, pathlib, sys; "
                    "print(json.dumps({"
                    "'nexus_file': str(pathlib.Path(nexus.__file__).resolve()), "
                    "'version': importlib.metadata.version('nexus-dashboard'), "
                    "'sys_path': sys.path}))"
                ),
            ],
            cwd=work_dir,
            env=env,
            deadline=deadline,
        )
        imported = _json_object(import_probe.stdout, command="installed-wheel import probe")
        imported_path = Path(imported["nexus_file"])
        if not imported_path.is_relative_to(venv_dir):
            raise RuntimeError(f"nexus imported outside isolated venv: {imported_path}")
        if imported["version"] != expected_version:
            raise RuntimeError(
                f"installed version {imported['version']} != expected {expected_version}"
            )
        checkout_src = (PROJECT_ROOT / "src").resolve()
        resolved_sys_path = {
            Path(item).resolve() for item in imported["sys_path"] if isinstance(item, str) and item
        }
        if checkout_src in resolved_sys_path:
            raise RuntimeError(f"repository source leaked into wheel smoke sys.path: {checkout_src}")

        demo = _run(
            [str(nexus_command), "demo", "--scenario", "v1-loop",
             "--data-dir", str(temp_root / "v1-demo"), "--json"],
            cwd=work_dir, env=env, deadline=deadline,
        )
        demo_payload = _json_object(demo.stdout, command="installed V1 demo")
        if demo_payload.get("ok") is not True:
            raise RuntimeError(f"installed V1 loop failed: {demo_payload}")

        runtime_probe = _run(
            [
                str(venv_python),
                "-c",
                (
                    "import json, pathlib; "
                    "from nexus.core.dashboard import app as dashboard; "
                    "print(json.dumps({"
                    "'data_dir': str(dashboard.default_config().data_dir), "
                    "'dashboard_file': str(pathlib.Path(dashboard.__file__).resolve())}))"
                ),
            ],
            cwd=work_dir,
            env=env,
            deadline=deadline,
        )
        runtime = _json_object(runtime_probe.stdout, command="installed dashboard/config probe")
        if runtime.get("data_dir") != str(data_dir):
            raise RuntimeError(
                f"installed dashboard config used {runtime.get('data_dir')!r}, "
                f"expected {str(data_dir)!r}"
            )
        dashboard_path = Path(runtime["dashboard_file"])
        if not dashboard_path.is_relative_to(venv_dir):
            raise RuntimeError(f"dashboard imported outside isolated venv: {dashboard_path}")

        sync = _run(
            [str(nexus_command), "sync", "--no-google"],
            cwd=work_dir,
            env=env,
            deadline=deadline,
        )
        receipt = _json_object(sync.stdout, command="nexus sync --no-google")
        if receipt.get("ok") is not True or receipt.get("status") != "succeeded":
            raise RuntimeError(f"offline sync did not succeed: {receipt}")
        if receipt.get("data_dir") != str(data_dir):
            raise RuntimeError(
                f"offline sync used {receipt.get('data_dir')!r}, expected {str(data_dir)!r}"
            )

        artifact_payloads: dict[str, Any] = {}
        for name, expected_type in ARTIFACT_TYPES.items():
            path = data_dir / name
            if not path.is_file():
                raise RuntimeError(f"offline sync did not create {path}")
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"offline sync created invalid JSON in {path}") from exc
            if type(payload) is not expected_type:
                raise RuntimeError(
                    f"{path} contains {type(payload).__name__}, expected {expected_type.__name__}"
                )
            artifact_payloads[name] = payload
        if artifact_payloads["sync_receipt.json"] != receipt:
            raise RuntimeError("persisted sync_receipt.json differs from CLI JSON")

        database_path = data_dir / "nexus.db"
        _run(
            [
                str(venv_python),
                "-c",
                (
                    "import os, pathlib, sqlite3; "
                    "p = pathlib.Path(os.environ['NEXUS_DATA_DIR']) / 'nexus.db'; "
                    "sqlite3.connect(p).execute('CREATE TABLE smoke (id INTEGER PRIMARY KEY)').connection.close()"
                ),
            ],
            cwd=work_dir,
            env=env,
            deadline=deadline,
        )
        if not database_path.is_file():
            raise RuntimeError(f"SQLite setup did not create {database_path}")
        backup = _run(
            [str(nexus_command), "db-backup"],
            cwd=work_dir,
            env=env,
            deadline=deadline,
        )
        backup_payload = _json_object(backup.stdout, command="nexus db-backup")
        if backup_payload.get("ok") is not True:
            raise RuntimeError(f"default-path backup did not succeed: {backup_payload}")
        backup_path = Path(backup_payload["archive"]).resolve()
        if not backup_path.is_file() or not backup_path.is_relative_to(data_dir):
            raise RuntimeError(f"backup was not created inside configured runtime: {backup_path}")
        backup_report_path = data_dir / "backup_report.json"
        backup_report = json.loads(backup_report_path.read_text(encoding="utf-8"))
        if backup_report.get("ok") is not True or backup_report.get("archive") != str(backup_path):
            raise RuntimeError(f"invalid runtime backup report: {backup_report}")
        unexpected_backup_outputs = [
            path
            for path in temp_root.rglob("backup_report.json")
            if path.resolve() != backup_report_path.resolve()
        ] + [
            path
            for path in temp_root.rglob("nexus-*.zip")
            if path.resolve() != backup_path
        ]
        if unexpected_backup_outputs:
            raise RuntimeError(
                "backup wrote outside configured runtime: "
                + ", ".join(str(path) for path in unexpected_backup_outputs)
            )

        return {
            "ok": True,
            "wheel": wheel.name,
            "version": expected_version,
            "imported_from_venv": True,
            "runtime_inherited_by_dashboard": True,
            "sync_status": receipt["status"],
            "artifacts": sorted(artifact_payloads),
            "backup_in_runtime": True,
            "v1_loop": demo_payload,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, default=Path("dist"))
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args(argv)
    try:
        report = run_smoke(
            dist_dir=args.dist_dir,
            timeout_seconds=args.timeout_seconds,
        )
    except Exception as exc:
        print(
            json.dumps(
                {"ok": False, "error_type": type(exc).__name__, "error": str(exc)},
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
