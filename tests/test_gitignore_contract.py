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


def test_local_agent_tooling_is_ignored() -> None:
    assert _is_ignored(ROOT / ".codex/agents/nexus-reviewer.toml")
    assert _is_ignored(ROOT / ".codex/session-state.json")
    assert _is_ignored(ROOT / ".claude/settings.local.json")
    assert _is_ignored(ROOT / "docs/CLAUDE.md")


def test_demo_runtime_directories_are_ignored() -> None:
    assert _is_ignored(ROOT / "nexus-data/sync_receipt.json")
    assert _is_ignored(ROOT / "nexus-data/feeds.json")
