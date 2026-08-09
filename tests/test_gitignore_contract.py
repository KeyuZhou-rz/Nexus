from __future__ import annotations

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def _is_ignored(path: Path) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "--quiet", "--no-index", str(path)],
        cwd=ROOT,
        check=False,
    )
    assert result.returncode in {0, 1}
    return result.returncode == 0


def test_codex_agent_roles_are_tracked_but_runtime_files_remain_ignored() -> None:
    assert not _is_ignored(ROOT / ".codex/agents/nexus-reviewer.toml")
    assert _is_ignored(ROOT / ".codex/agents/secret.env")
    assert _is_ignored(ROOT / ".codex/agents/cache/token")
    assert _is_ignored(ROOT / ".codex/session-state.json")


def test_private_beta_runtime_directory_is_ignored() -> None:
    assert _is_ignored(ROOT / "nexus-data/sync_receipt.json")
    assert _is_ignored(ROOT / "nexus-data/feeds.json")
