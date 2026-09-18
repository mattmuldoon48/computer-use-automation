"""Architectural invariant: the entire replay dependency graph is model-free."""

import ast
from pathlib import Path


def test_replay_transitive_imports_exclude_models_and_discovery():
    root = Path(__file__).parents[2] / "src" / "ui_capability"
    forbidden = {"openai", "anthropic", "discovery", "compiler", "provider", "models", "litellm"}
    visited = set()

    def visit(path):
        if path in visited:
            return
        visited.add(path)
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
                if node.level:
                    base = path.parent
                    for _ in range(node.level - 1):
                        base = base.parent
                    relative = base.joinpath(*(node.module or "").split("."))
                    if relative.with_suffix(".py").is_file():
                        visit(relative.with_suffix(".py"))
                elif (node.module or "").startswith("ui_capability."):
                    relative = root.joinpath(*node.module.split(".")[1:]).with_suffix(".py")
                    if relative.is_file():
                        visit(relative)
            else:
                continue
            for name in names:
                assert not forbidden.intersection(name.split(".")), (path, name)
        # Dynamic imports would evade the graph and have no reason to exist here.
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", getattr(node.func, "attr", ""))
                assert name not in {"__import__", "import_module", "eval", "exec"}, path

    visit(root / "replay.py")
