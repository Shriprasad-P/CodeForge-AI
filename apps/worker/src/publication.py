"""Trusted Phase 7 publication worker.

The untrusted coding sandbox never receives GitHub credentials. This module runs
on the worker host, applies the persisted approved patch to a fresh checkout,
verifies the base and diff fingerprints, then performs one commit/push/PR flow.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import get_session_factory
from app.models.agent_run import AgentRun, AgentRunErrorType, AgentRunStatus
from app.models.github import GitHubInstallation, RepositoryConnection
from app.services.agent_events import AgentEventPublisher
from app.services.github_client import GitHubClient
from sandbox_sdk import SandboxSpec
from sandbox_sdk.docker_provider import DockerSandboxProvider

from src.agent.tools import AgentTools
from src.checkout import prepare_fixture_checkout, sanitize_text

logger = get_logger(__name__)


def _git(repo: Path, args: list[str], *, check: bool = True, token: str | None = None) -> str:
    env = None
    if token:
        import os

        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=check,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(sanitize_text(exc.stderr or exc.stdout or "git operation failed", token)) from None
    return (result.stdout or "").strip()


def _safe_branch(run_id: UUID) -> str:
    return f"agentdock/run-{str(run_id).replace('-', '')[:12]}"


def _safe_title(summary: str | None, run_id: UUID) -> str:
    value = re.sub(r"\s+", " ", (summary or "").replace("\n", " ")).strip()
    value = value[:120].strip()
    return value or f"AgentDock changes for run {str(run_id)[:8]}"


def _validate_with_sandbox(provider: DockerSandboxProvider, repo: Path, run: AgentRun) -> tuple[bool, str]:
    command = (run.validation or {}).get("command") if isinstance(run.validation, dict) else None
    if not isinstance(command, list) or not command:
        return True, "No recorded validation command"
    settings = get_settings()
    sandbox_id: str | None = None
    try:
        sandbox_id = provider.create(
            SandboxSpec(
                image=settings.sandbox_image,
                execution_id=f"publication-{run.id}",
                memory_limit=settings.sandbox_memory_limit,
                nano_cpus=int(settings.sandbox_cpu_limit * 1_000_000_000),
                pids_limit=settings.sandbox_pids_limit,
                network_disabled=settings.sandbox_network_disabled,
            )
        )
        provider.put_directory(sandbox_id, str(repo), "/workspace")
        result = AgentTools(provider, sandbox_id).run_command([str(item) for item in command])
        return result.ok, result.summary[:2000]
    finally:
        if sandbox_id:
            provider.destroy(sandbox_id)
            provider.destroy_labeled(execution_id=f"publication-{run.id}")


async def process_publication(run_id: UUID, provider: DockerSandboxProvider) -> None:
    settings = get_settings()
    factory = get_session_factory()
    events = AgentEventPublisher(run_id)
    work_dir = Path(tempfile.mkdtemp(prefix=f"agentdock-publication-{run_id}-"))
    token: str | None = None
    repo_path = work_dir / "repo"
    try:
        async with factory() as db:
            result = await db.execute(
                update(AgentRun)
                .where(
                    AgentRun.id == run_id,
                    AgentRun.approval_status == "approved",
                    AgentRun.publication_status.in_(["approved", "publication_failed"]),
                    AgentRun.status.in_([AgentRunStatus.awaiting_approval, AgentRunStatus.publishing]),
                )
                .values(status=AgentRunStatus.publishing, publication_status="publishing")
                .returning(AgentRun.id)
            )
            await db.commit()
            if result.first() is None:
                return
            run = await db.get(AgentRun, run_id)
            connection = await db.scalar(
                select(RepositoryConnection).where(
                    RepositoryConnection.id == run.repository_connection_id,
                    RepositoryConnection.user_id == run.user_id,
                    RepositoryConnection.is_active.is_(True),
                )
            )
            installation = await db.get(GitHubInstallation, connection.installation_id) if connection else None
            if run.publication_status == "published" or run.github_pr_url:
                return
            if not connection or not installation or installation.suspended_at is not None:
                raise RuntimeError("Repository connection is no longer authorized")
            diff_text = run.diff_text or ""
            if hashlib.sha256(diff_text.encode("utf-8")).hexdigest() != run.diff_hash:
                raise RuntimeError("Approved diff fingerprint no longer matches")
            branch = run.branch_name or _safe_branch(run.id)
            run.branch_name = branch
            await db.commit()

        await events.publish("publication.started", {"branch": branch})
        test_remote = settings.publication_test_remote_url if settings.app_env.lower() in {"local", "test", "development"} else ""
        if test_remote:
            subprocess.run(["git", "clone", "--branch", connection.default_branch, test_remote, str(repo_path)], check=True, capture_output=True, text=True, timeout=120)
        else:
            client = GitHubClient()
            token = await client.create_installation_token(installation.github_installation_id)
            remote = f"https://github.com/{connection.owner}/{connection.name}.git"
            clone_url = f"https://x-access-token:{token}@github.com/{connection.owner}/{connection.name}.git"
            try:
                subprocess.run(["git", "clone", "--branch", connection.default_branch, clone_url, str(repo_path)], check=True, capture_output=True, text=True, timeout=120)
            except subprocess.CalledProcessError as exc:
                raise RuntimeError(sanitize_text(exc.stderr or "repository clone failed", token)) from None
            _git(repo_path, ["remote", "set-url", "origin", remote], token=token)

        actual_base = _git(repo_path, ["rev-parse", "HEAD"])
        if run.base_commit_sha and actual_base != run.base_commit_sha:
            raise RuntimeError("repository_changed")
        patch_text = diff_text if diff_text.endswith("\n") else f"{diff_text}\n"
        apply = subprocess.run(["git", "-C", str(repo_path), "apply", "-"], input=patch_text, text=True, capture_output=True)
        if apply.returncode != 0:
            raise RuntimeError(f"approved patch no longer applies cleanly: {sanitize_text(apply.stderr or apply.stdout)}")
        actual_diff = _git(repo_path, ["diff"])
        if hashlib.sha256(actual_diff.encode("utf-8")).hexdigest() != run.diff_hash:
            raise RuntimeError("approved diff fingerprint mismatch")
        await events.publish("publication.validation.started", {})
        valid, validation_output = _validate_with_sandbox(provider, repo_path, run)
        await events.publish("publication.validation.completed", {"ok": valid})
        if not valid:
            raise RuntimeError(f"publication validation failed: {validation_output[:500]}")

        existing_commit = None
        if run.commit_sha:
            _git(repo_path, ["fetch", "origin", f"{branch}:{branch}"], check=False)
            existing_commit = _git(repo_path, ["rev-parse", "--verify", branch], check=False)
        if existing_commit:
            if existing_commit != run.commit_sha:
                raise RuntimeError("publication branch already points to a different commit")
            _git(repo_path, ["checkout", branch])
            commit_sha = existing_commit
        else:
            _git(repo_path, ["checkout", "-b", branch])
            _git(repo_path, ["config", "user.name", settings.git_author_name])
            _git(repo_path, ["config", "user.email", settings.git_author_email])
            title = _safe_title(run.summary, run.id)
            _git(repo_path, ["add", "-A"])
            _git(repo_path, ["commit", "-m", title, "-m", f"AgentDock run: {run.id}"])
            commit_sha = _git(repo_path, ["rev-parse", "HEAD"])
        title = _safe_title(run.summary, run.id)
        async with factory() as db:
            persisted = await db.get(AgentRun, run_id)
            persisted.commit_sha = commit_sha
            await db.commit()
        await events.publish("publication.commit.created", {"commit_sha": commit_sha})

        push_args = ["push", "origin", f"{branch}:{branch}"]
        if token:
            import base64

            auth = base64.b64encode(f"x-access-token:{token}".encode()).decode()
            subprocess.run(["git", "-C", str(repo_path), "-c", f"http.extraheader=AUTHORIZATION: basic {auth}", *push_args], check=True, capture_output=True, text=True, timeout=120)
        else:
            _git(repo_path, push_args)
        await events.publish("publication.branch.pushed", {"branch": branch})

        pr: dict = {}
        if settings.publication_mock_prs:
            pr = {"id": 1, "number": 1, "html_url": None}
        else:
            client = GitHubClient()
            pr = await client.find_pull_request(token or "", owner=connection.owner, repo=connection.name, head=branch, base=connection.default_branch)
            if pr is None:
                pr = await client.create_pull_request(
                    token or "",
                    owner=connection.owner,
                    repo=connection.name,
                    title=title,
                    body=f"## Summary\n\n{(run.summary or run.task)[:4000]}\n\n## Changes\n\n{len(run.changed_files or [])} changed file(s).\n\n## Validation\n\nRecorded validation completed successfully.\n\n## AgentDock Run\n\n`{run.id}`\n\nBase commit: `{run.base_commit_sha}`",
                    head=branch,
                    base=connection.default_branch,
                )
        async with factory() as db:
            persisted = await db.get(AgentRun, run_id)
            persisted.status = AgentRunStatus.succeeded
            persisted.publication_status = "published"
            persisted.github_pr_id = int(pr.get("id")) if pr.get("id") else None
            persisted.github_pr_number = int(pr.get("number")) if pr.get("number") else None
            persisted.github_pr_url = pr.get("html_url")
            persisted.finished_at = datetime.now(timezone.utc)
            await db.commit()
        await events.publish("publication.pr.created", {"number": pr.get("number"), "url": pr.get("html_url")})
    except Exception as exc:
        message = str(exc)[:1024]
        logger.warning("publication.failed", run_id=str(run_id), reason=sanitize_text(message, token))
        async with factory() as db:
            persisted = await db.get(AgentRun, run_id)
            if persisted:
                persisted.status = AgentRunStatus.failed
                persisted.publication_status = "publication_failed"
                persisted.error_type = AgentRunErrorType.repository_changed if message == "repository_changed" else AgentRunErrorType.publication_failed
                persisted.error_message = sanitize_text(message, token)
                await db.commit()
        await events.publish("publication.failed", {"error": sanitize_text(message, token)})
    finally:
        if token:
            token = None
        shutil.rmtree(work_dir, ignore_errors=True)
