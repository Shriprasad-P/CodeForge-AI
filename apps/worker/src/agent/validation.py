"""Safe, deterministic discovery of repository validation commands."""

from __future__ import annotations

import json
from pathlib import Path


def discover_validation_command(repo: Path) -> list[str] | None:
    """Return an argv-only validation command supported by the sandbox policy.

    Discovery is intentionally conservative: only repository metadata that is
    already present in the checkout is considered, and no shell text is ever
    constructed from repository contents.
    """
    package_json = repo / "package.json"
    if package_json.is_file():
        try:
            package = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            package = {}
        scripts = package.get("scripts") if isinstance(package, dict) else None
        if isinstance(scripts, dict):
            if isinstance(scripts.get("test"), str) and scripts.get("test", "").strip():
                return ["npm", "test"]

    if (repo / "Makefile").is_file():
        try:
            makefile = (repo / "Makefile").read_text(encoding="utf-8", errors="replace")
        except OSError:
            makefile = ""
        if any(line.startswith("test:") for line in makefile.splitlines()):
            return ["make", "test"]

    python_markers = {"pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml"}
    if any((repo / marker).is_file() for marker in python_markers):
        return ["python", "-m", "pytest", "-q"]
    tests_dir = repo / "tests"
    if tests_dir.is_dir():
        try:
            if any(path.is_file() and (path.name.startswith("test_") or path.name.endswith("_test.py")) for path in tests_dir.iterdir()):
                return ["python", "-m", "pytest", "-q"]
        except OSError:
            pass
    try:
        if any(path.is_file() and (path.name.startswith("test_") or path.name.endswith("_test.py")) for path in repo.iterdir()):
            return ["python", "-m", "pytest", "-q"]
    except OSError:
        pass
    return None
