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
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import update

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.observability import bind_observability, claim_ref, classify_error, clear_observability, metrics, persist_metric, safe_error
from app.db.session import get_session_factory
from app.models.agent_run import AGENT_TERMINAL, AgentRun, AgentRunErrorType, AgentRunStatus
from app.services.agent_events import AgentEventPublisher
from app.services.github_client import GitHubClient
from sandbox_sdk import SandboxSpec
from sandbox_sdk.docker_provider import DockerSandboxProvider

from src.agent.tools import AgentTools
from src.artifacts import PUBLICATION_ARTIFACT_VERSION, ArtifactCaptureError, capture_publication_artifact
from src.checkout import clone_github_repo, github_credential_env, sanitize_text
from src.authorization import RepositoryRevokedError, require_repository_authorized
from src.delivery import DeliveryClaim, DeliveryClaimLost
from src.runtime import run_blocking

logger = get_logger(__name__)


async def reconcile_pending_publications() -> None:
    """Compatibility wrapper for the shared PostgreSQL delivery reconciler."""
    from src.delivery import reconcile_durable_delivery

    await reconcile_durable_delivery()


def _git(repo: Path, args: list[str], *, check: bool = True, token: str | None = None) -> str:
    try:
        if token:
            with github_credential_env(token) as env:
                result = subprocess.run(
                    ["git", "-C", str(repo), *args],
                    check=check,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    env=env,
                )
        else:
            result = subprocess.run(
                ["git", "-C", str(repo), *args],
                check=check,
                capture_output=True,
                text=True,
                timeout=120,
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


async def _validate_with_sandbox(provider: DockerSandboxProvider, repo: Path, run: AgentRun) -> tuple[bool, str]:
    command = (run.validation or {}).get("command") if isinstance(run.validation, dict) else None
    if not isinstance(command, list) or not command:
        return False, "Validation command is required"
    settings = get_settings()
    sandbox_id: str | None = None
    try:
        sandbox_id = await run_blocking(provider.create,
            SandboxSpec(
                image=settings.sandbox_image,
                execution_id=f"publication-{run.id}",
                memory_limit=settings.sandbox_memory_limit,
                nano_cpus=int(settings.sandbox_cpu_limit * 1_000_000_000),
                pids_limit=settings.sandbox_pids_limit,
                network_disabled=settings.sandbox_network_disabled,
            )
        )
        await run_blocking(provider.put_directory, sandbox_id, str(repo), "/workspace")
        result = await AgentTools(provider, sandbox_id).run_command_async(
            [str(item) for item in command],
            timeout=float(settings.agent_tool_timeout_seconds),
        )
        return result.ok, result.summary[:2000]
    finally:
        if sandbox_id:
            await run_blocking(provider.destroy, sandbox_id)
        await run_blocking(provider.destroy_labeled, execution_id=f"publication-{run.id}")


async def process_publication(
    run_id: UUID,
    provider: DockerSandboxProvider,
    delivery_claim: DeliveryClaim | None = None,
) -> None:
    settings = get_settings()
    factory = get_session_factory()
    events = AgentEventPublisher(run_id)
    claim_token = delivery_claim.token if delivery_claim is not None else uuid4().hex
    work_dir = Path(tempfile.mkdtemp(prefix=f"agentdock-publication-{run_id}-"))
    token: str | None = None
    repo_path = work_dir / "repo"
    started_clock = time.perf_counter()
    try:
        async with factory() as db:
            claim_update = update(AgentRun).where(
                AgentRun.id == run_id,
                AgentRun.approval_status == "approved",
                AgentRun.publication_status.in_(["approved", "publication_failed", "publishing"]),
                AgentRun.status.in_([AgentRunStatus.awaiting_approval, AgentRunStatus.publishing, AgentRunStatus.failed]),
                AgentRun.cancel_requested.is_(False),
            )
            if delivery_claim is not None:
                claim_update = claim_update.where(AgentRun.delivery_claim_token == delivery_claim.token)
            result = await db.execute(
                claim_update
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
            if run.publication_status == "published" or run.github_pr_url:
                return
            publication_attempt_id = f"{run.id}:{delivery_claim.attempt_count if delivery_claim else 1}"
            bind_observability(
                workflow_correlation_id=str(run.workflow_correlation_id),
                agent_run_id=str(run.id),
                repository_connection_id=str(run.repository_connection_id),
                publication_attempt_id=publication_attempt_id,
                claim_ref=claim_ref(delivery_claim.token) if delivery_claim else None,
            )
            metrics.inc("agentdock_publication_attempts_total")
            await persist_metric("agentdock_publication_attempts_total")
            logger.info(
                "publication.started",
                publication_attempt_id=publication_attempt_id,
                agent_run_id=str(run.id),
                repository_connection_id=str(run.repository_connection_id),
            )
            connection, installation = await require_repository_authorized(
                db,
                user_id=run.user_id,
                connection_id=run.repository_connection_id,
            )
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
            branch_update = update(AgentRun).where(AgentRun.id == run_id)
            if delivery_claim is not None:
                branch_update = branch_update.where(AgentRun.delivery_claim_token == delivery_claim.token)
            changed = await db.execute(branch_update.values(branch_name=branch))
            if delivery_claim is not None and changed.rowcount != 1:
                await db.rollback()
                raise DeliveryClaimLost("publication delivery claim lost")
            await db.commit()

        async def assert_claim() -> None:
            async with factory() as check_db:
                claim_check = update(AgentRun).where(
                    AgentRun.id == run_id,
                    AgentRun.publication_status == "publishing",
                    AgentRun.publication_claim_token == claim_token,
                    AgentRun.cancel_requested.is_(False),
                )
                if delivery_claim is not None:
                    claim_check = claim_check.where(AgentRun.delivery_claim_token == delivery_claim.token)
                changed = await check_db.execute(
                    claim_check
                    .values(updated_at=datetime.now(timezone.utc))
                )
                await check_db.commit()
            if changed.rowcount != 1:
                raise RuntimeError("publication_claim_lost")

        async def assert_repository_authorized() -> None:
            nonlocal connection, installation
            async with factory() as check_db:
                connection, installation = await require_repository_authorized(
                    check_db,
                    user_id=run.user_id,
                    connection_id=run.repository_connection_id,
                )

        await events.publish("publication.started", {"branch": branch})
        await assert_claim()
        await assert_repository_authorized()
        test_remote = settings.publication_test_remote_url if settings.app_env.lower() in {"local", "test", "development"} else ""
        if test_remote:
            subprocess.run(["git", "clone", "--branch", connection.default_branch, test_remote, str(repo_path)], check=True, capture_output=True, text=True, timeout=120)
        else:
            client = GitHubClient()
            await assert_repository_authorized()
            token = await client.create_installation_token(installation.github_installation_id)
            await assert_repository_authorized()
            clone_github_repo(
                dest=repo_path,
                owner=connection.owner,
                name=connection.name,
                default_branch=connection.default_branch,
                installation_token=token,
            )

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
        await assert_repository_authorized()
        await events.publish("publication.validation.started", {})
        valid, validation_output = await _validate_with_sandbox(provider, repo_path, run)
        await events.publish("publication.validation.completed", {"ok": valid})
        if not valid:
            raise RuntimeError(f"publication validation failed: {validation_output[:500]}")

        # Always inspect the remote branch. A worker can crash after the push
        # succeeds but before commit_sha is persisted; retrying must adopt the
        # already-pushed commit instead of creating a second branch history.
        _git(repo_path, ["fetch", "origin", f"{branch}:{branch}"], check=False, token=token)
        existing_commit = _git(repo_path, ["rev-parse", "--verify", branch], check=False)
        if existing_commit:
            # A crash can happen after the remote push but before commit_sha
            # is persisted. In that case, adopt the already-pushed commit
            # after the artifact comparison below instead of creating a
            # second commit. If a commit was persisted, it must match exactly.
            if run.commit_sha and existing_commit != run.commit_sha:
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
            commit_update = update(AgentRun).where(
                AgentRun.id == run_id,
                AgentRun.publication_claim_token == claim_token,
            )
            if delivery_claim is not None:
                commit_update = commit_update.where(AgentRun.delivery_claim_token == delivery_claim.token)
            changed = await db.execute(
                commit_update
                .values(commit_sha=commit_sha)
            )
            if changed.rowcount != 1:
                await db.rollback()
                raise RuntimeError("publication_claim_lost")
            await db.commit()
        await events.publish("publication.commit.created", {"commit_sha": commit_sha})

        await assert_claim()
        await assert_repository_authorized()
        push_args = ["push", "origin", f"{branch}:{branch}"]
        _git(repo_path, push_args, token=token)
        await events.publish("publication.branch.pushed", {"branch": branch})

        pr: dict = {}
        if settings.publication_mock_prs:
            pr = {"id": 1, "number": 1, "html_url": None}
        else:
            await assert_claim()
            await assert_repository_authorized()
            client = GitHubClient()
            pr = await client.find_pull_request(token or "", owner=connection.owner, repo=connection.name, head=branch, base=connection.default_branch)
            if pr is None:
                await assert_claim()
                await assert_repository_authorized()
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
            final_update = update(AgentRun).where(
                AgentRun.id == run_id,
                AgentRun.publication_claim_token == claim_token,
                AgentRun.cancel_requested.is_(False),
            )
            if delivery_claim is not None:
                final_update = final_update.where(AgentRun.delivery_claim_token == delivery_claim.token)
            changed = await db.execute(
                final_update
                .values(
                    status=AgentRunStatus.succeeded,
                    publication_status="published",
                    delivery_claim_token=None,
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
        metrics.inc("agentdock_publication_success_total")
        await persist_metric("agentdock_publication_success_total")
        logger.info(
            "publication.completed",
            publication_attempt_id=publication_attempt_id,
            agent_run_id=str(run_id),
            state_from="publishing",
            state_to="succeeded",
            duration_ms=int((time.perf_counter() - started_clock) * 1000),
            retryable=False,
            terminal=True,
        )
    except Exception as exc:
        message = safe_error(exc, 1024)
        metrics.inc("agentdock_publication_failures_total")
        await persist_metric("agentdock_publication_failures_total")
        logger.warning(
            "publication.failed",
            agent_run_id=str(run_id),
            publication_attempt_id=locals().get("publication_attempt_id"),
            error_class=classify_error(exc),
            retryable=True,
            duration_ms=int((time.perf_counter() - started_clock) * 1000),
        )
        async with factory() as db:
            failure_update = update(AgentRun).where(
                AgentRun.id == run_id,
                AgentRun.publication_claim_token == claim_token,
                AgentRun.status.not_in(tuple(AGENT_TERMINAL)),
            )
            if delivery_claim is not None:
                failure_update = failure_update.where(AgentRun.delivery_claim_token == delivery_claim.token)
            revoked = isinstance(exc, RepositoryRevokedError)
            changed = await db.execute(
                failure_update
                .values(
                    status=AgentRunStatus.repository_revoked if revoked else AgentRunStatus.failed,
                    publication_status="revoked" if revoked else "publication_failed",
                    delivery_claim_token=None,
                    publication_claim_token=None,
                    error_type=AgentRunErrorType.repository_revoked if revoked else AgentRunErrorType.repository_changed if message == "repository_changed" else AgentRunErrorType.publication_failed,
                    error_message="Repository authorization revoked" if revoked else sanitize_text(message, token),
                )
            )
            if changed.rowcount:
                await db.commit()
            else:
                await db.rollback()
        await events.publish("publication.failed", {"error": "Repository authorization revoked" if isinstance(exc, RepositoryRevokedError) else sanitize_text(message, token)})
    finally:
        if token:
            token = None
        shutil.rmtree(work_dir, ignore_errors=True)
        clear_observability()
