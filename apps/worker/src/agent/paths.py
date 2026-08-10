"""Workspace path validation for agent tools."""

from __future__ import annotations

from pathlib import PurePosixPath


class PathEscapeError(ValueError):
    pass


def safe_rel_path(path: str) -> str:
    raw = (path or "").replace("\\", "/").strip()
    if not raw or raw == ".":
        return "."
    if raw.startswith("/") or raw.startswith("~"):
        raise PathEscapeError("absolute paths are not allowed")
    parts = [p for p in PurePosixPath(raw).parts if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise PathEscapeError("path traversal is not allowed")
    if parts and parts[0] == ".git":
        raise PathEscapeError(".git is not accessible")
    if any(p == ".git" for p in parts):
        raise PathEscapeError(".git is not accessible")
    return str(PurePosixPath(*parts)) if parts else "."


def workspace_path(path: str) -> str:
    rel = safe_rel_path(path)
    if rel == ".":
        return "/workspace"
    return f"/workspace/{rel}"
