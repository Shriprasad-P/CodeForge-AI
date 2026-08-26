"""Trusted Phase 7 publication worker.

The untrusted coding sandbox never receives GitHub credentials. This module runs
on the worker host, applies the persisted approved patch to a fresh checkout,
verifies the base and diff fingerprints, then performs one commit/push/PR flow.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import get_session_factory
from app.models.agent_run import AgentRun, AgentRunErrorType, AgentRunStatus
from app.models.github import GitHubInstallation, RepositoryConnection
from app.services.agent_events import AgentEventPublisher
from app.services.github_client import GitHubClient
from app.services.queue import enqueue_publication
from sandbox_sdk import SandboxSpec
from sandbox_sdk.docker_provider import DockerSandboxProvider

from src.agent.tools import AgentTools
from src.artifacts import PUBLICATION_ARTIFACT_VERSION, ArtifactCaptureError, capture_publication_artifact
from src.checkout import sanitize_text

logger = get_logger(__name__)


async def reconcile_pending_publications() -> None:
    """Recover approved jobs after worker/Redis crashes.

    PostgreSQL remains authoritative: an approved run is re-enqueued on worker
    startup, while claims left in ``publishing`` are returned to the approved
    state only after a bounded lease has expired.
    """
    factory = get_session_factory()
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
    async with factory() as db:
        stale = await db.execute(
            select(AgentRun.id).where(
                AgentRun.approval_status == "approved",
                AgentRun.publication_status == "publishing",
                AgentRun.updated_at < cutoff,
            )
        )
        stale_ids = list(stale.scalars())
        if stale_ids:
            await db.execute(
                update(AgentRun)
                .where(AgentRun.id.in_(stale_ids))
                .values(status=AgentRunStatus.awaiting_approval, publication_status="approved")
            )
        pending = await db.execute(
            select(AgentRun.id).where(
                AgentRun.approval_status == "approved",
                AgentRun.publication_status.in_(["approved", "publication_failed"]),
                AgentRun.status.in_([AgentRunStatus.awaiting_approval, AgentRunStatus.failed]),
            )
        )
        pending_ids = list(pending.scalars())
        await db.commit()
    for publication_id in {*(stale_ids or []), *(pending_ids or [])}:
        try:
            await enqueue_publication(publication_id)
        except Exception:  # noqa: BLE001
            logger.warning("publication.requeue_failed", run_id=str(publication_id))


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


def _committed_artifact(repo: Path, base_sha: str, branch: str) -> bytes:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "diff",
            "--binary",
            "--full-index",
            "--no-ext-diff",
            "--no-renames",
            f"{base_sha}..{branch}",
            "--",
        ],
        check=False,
        capture_output=True,
        timeout=120,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or b"branch verification failed").decode("utf-8", errors="replace")
        raise RuntimeError(sanitize_text(detail))
    return result.stdout


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
    claim_token = uuid4().hex
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
                    AgentRun.status.in_([AgentRunStatus.awaiting_approval, AgentRunStatus.publishing, AgentRunStatus.failed]),
                )
                .values(
                    status=AgentRunStatus.publishing,
                    publication_status="publishing",
                    publication_claim_token=claim_token,
                )
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
            artifact = bytes(run.publication_artifact) if run.publication_artifact is not None else None
            artifact_hash = run.publication_artifact_hash
            if (
                run.publication_artifact_status != "ready"
                or artifact is None
                or not artifact_hash
                or run.publication_artifact_size is None
                or run.publication_artifact_version != PUBLICATION_ARTIFACT_VERSION
                or not isinstance(run.publication_change_manifest, list)
            ):
                raise RuntimeError("approved publication artifact is unavailable")
            if run.approval_artifact_hash != artifact_hash or run.approval_artifact_version != run.publication_artifact_version:
                raise RuntimeError("approval does not match the publication artifact")
            if run.approval_base_commit_sha != run.base_commit_sha:
                raise RuntimeError("approval base commit binding is stale")
            if len(artifact) != run.publication_artifact_size:
                raise RuntimeError("approved publication artifact size mismatch")
            if not hmac.compare_digest(hashlib.sha256(artifact).hexdigest(), artifact_hash):
                raise RuntimeError("approved publication artifact hash mismatch")
            if run.diff_hash != artifact_hash or run.validation_artifact_hash != artifact_hash:
                raise RuntimeError("approved artifact validation binding is stale")
            if not isinstance(run.validation, dict) or run.validation.get("ok") is not True:
                raise RuntimeError("approved artifact has no successful validation")
            approved_manifest = run.publication_change_manifest
            branch = run.branch_name or _safe_branch(run.id)
            run.branch_name = branch
            await db.commit()

        async def assert_claim() -> None:
            async with factory() as check_db:
                changed = await check_db.execute(
                    update(AgentRun)
                    .where(
                        AgentRun.id == run_id,
                        AgentRun.publication_status == "publishing",
                        AgentRun.publication_claim_token == claim_token,
                    )
                    .values(updated_at=datetime.now(timezone.utc))
                )
                await check_db.commit()
            if changed.rowcount != 1:
                raise RuntimeError("publication_claim_lost")

        await events.publish("publication.started", {"branch": branch})
        await assert_claim()
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
        apply = subprocess.run(
            ["git", "-C", str(repo_path), "apply", "--binary", "--whitespace=nowarn", "-"],
            input=artifact,
            capture_output=True,
        )
        if apply.returncode != 0:
            detail = (apply.stderr or apply.stdout or b"patch application failed").decode("utf-8", errors="replace")
            raise RuntimeError(f"approved artifact no longer applies cleanly: {sanitize_text(detail)}")
        try:
            reproduced = capture_publication_artifact(
                repo_path,
                base_sha=run.base_commit_sha or "",
                max_artifact_bytes=settings.agent_max_publication_artifact_bytes,
                max_preview_chars=settings.agent_max_diff_preview_chars,
            )
        except ArtifactCaptureError as exc:
            raise RuntimeError(f"resulting publication state could not be verified: {exc}") from None
        if (
            reproduced.patch != artifact
            or not hmac.compare_digest(reproduced.artifact_hash, artifact_hash)
            or reproduced.manifest != approved_manifest
        ):
            raise RuntimeError("resulting repository change does not match the approved artifact")
        await assert_claim()
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
            if _committed_artifact(repo_path, run.base_commit_sha or "", branch) != artifact:
                raise RuntimeError("publication branch does not contain the approved artifact")
            # This is a fresh temporary checkout; force checkout is safe only
            # after the branch tree has matched the approved artifact exactly.
            _git(repo_path, ["checkout", "--force", branch])
            commit_sha = existing_commit
        else:
            await assert_claim()
            _git(repo_path, ["checkout", "-b", branch])
            _git(repo_path, ["config", "user.name", settings.git_author_name])
            _git(repo_path, ["config", "user.email", settings.git_author_email])
            title = _safe_title(run.summary, run.id)
            _git(repo_path, ["add", "-A"])
            _git(repo_path, ["commit", "-m", title, "-m", f"AgentDock run: {run.id}"])
            commit_sha = _git(repo_path, ["rev-parse", "HEAD"])
        title = _safe_title(run.summary, run.id)
        async with factory() as db:
            changed = await db.execute(
                update(AgentRun)
                .where(AgentRun.id == run_id, AgentRun.publication_claim_token == claim_token)
                .values(commit_sha=commit_sha)
            )
            if changed.rowcount != 1:
                await db.rollback()
                raise RuntimeError("publication_claim_lost")
            await db.commit()
        await events.publish("publication.commit.created", {"commit_sha": commit_sha})

        await assert_claim()
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
            await assert_claim()
            client = GitHubClient()
            pr = await client.find_pull_request(token or "", owner=connection.owner, repo=connection.name, head=branch, base=connection.default_branch)
            if pr is None:
                await assert_claim()
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
            changed = await db.execute(
                update(AgentRun)
                .where(AgentRun.id == run_id, AgentRun.publication_claim_token == claim_token)
                .values(
                    status=AgentRunStatus.succeeded,
                    publication_status="published",
                    publication_claim_token=None,
                    github_pr_id=int(pr.get("id")) if pr.get("id") else None,
                    github_pr_number=int(pr.get("number")) if pr.get("number") else None,
                    github_pr_url=pr.get("html_url"),
                    finished_at=datetime.now(timezone.utc),
                )
            )
            if changed.rowcount != 1:
                await db.rollback()
                raise RuntimeError("publication_claim_lost")
            await db.commit()
        await events.publish("publication.pr.created", {"number": pr.get("number"), "url": pr.get("html_url")})
    except Exception as exc:
        message = str(exc)[:1024]
        logger.warning("publication.failed", run_id=str(run_id), reason=sanitize_text(message, token))
        async with factory() as db:
            changed = await db.execute(
                update(AgentRun)
                .where(AgentRun.id == run_id, AgentRun.publication_claim_token == claim_token)
                .values(
                    status=AgentRunStatus.failed,
                    publication_status="publication_failed",
                    publication_claim_token=None,
                    error_type=AgentRunErrorType.repository_changed if message == "repository_changed" else AgentRunErrorType.publication_failed,
                    error_message=sanitize_text(message, token),
                )
            )
            if changed.rowcount:
                await db.commit()
            else:
                await db.rollback()
        await events.publish("publication.failed", {"error": sanitize_text(message, token)})
    finally:
        if token:
            token = None
        shutil.rmtree(work_dir, ignore_errors=True)
