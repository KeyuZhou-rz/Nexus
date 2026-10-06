"""nexus.core is the personal loop; it must not import optional subsystems."""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "src" / "nexus" / "core"


def _imports(path: Path) -> list[str]:
    module = path.relative_to(CORE.parent.parent).with_suffix("").parts
    package = list(module if path.name == "__init__.py" else module[:-1])
    if package and package[-1] == "__init__":
        package.pop()
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                anchor = package[: len(package) - node.level + 1]
                found.append(".".join(anchor + ([node.module] if node.module else [])))
            elif node.module:
                found.append(node.module)
    return found


def test_core_imports_only_core() -> None:
    leaks = [
        f"{path.relative_to(CORE)} -> {name}"
        for path in sorted(CORE.rglob("*.py"))
        for name in _imports(path)
        if (name == "nexus" or name.startswith("nexus."))
        and name != "nexus.core"
        and not name.startswith("nexus.core.")
    ]
    assert leaks == []


def test_dashboard_import_does_not_load_knowledge_stack() -> None:
    code = (
        "import sys, nexus.core.dashboard.app, nexus.core.sync_service;"
        "heavy = {'chromadb', 'litellm', 'nexus.knowledge', 'nexus.intelligence.briefing'};"
        "print(sorted(heavy & set(sys.modules)))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        env={"PYTHONPATH": str(CORE.parents[1])},
    ).stdout.strip()
    assert out == "[]"
