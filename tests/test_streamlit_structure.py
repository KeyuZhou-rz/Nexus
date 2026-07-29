from __future__ import annotations

import ast
from pathlib import Path


APP_PATH = Path(__file__).parents[1] / "src" / "nexus" / "streamlit_app.py"


def _is_streamlit_expander(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "st"
        and node.func.attr == "expander"
    )


class _NestedExpanderVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.depth = 0
        self.nested: list[int] = []

    def visit_With(self, node: ast.With) -> None:
        is_expander = any(_is_streamlit_expander(item.context_expr) for item in node.items)
        if is_expander and self.depth:
            self.nested.append(node.lineno)
        if is_expander:
            self.depth += 1
        self.generic_visit(node)
        if is_expander:
            self.depth -= 1


def test_streamlit_app_never_nests_expanders() -> None:
    visitor = _NestedExpanderVisitor()
    visitor.visit(ast.parse(APP_PATH.read_text(encoding="utf-8")))
    assert visitor.nested == []
