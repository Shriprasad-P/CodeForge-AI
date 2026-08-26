from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app import create_app
from app.core.config import get_settings, settings
from app.models.github import GitHubInstallation, RepositoryConnection
from app.models.user import User
from app.services.agent_events import (
    build_event,
    publish_agent_event_sync,
    serialize_event,
)


def _sync_engine():
    return create_engine(get_settings().sync_database_url)


@pytest.fixture
def ws_api():
    """Sync TestClient — WebSocket tests must not share the async pytest loop."""
    import os

    os.environ["AUTH_RATE_LIMIT_ATTEMPTS"] = "1000"
    os.environ["EXECUTION_QUEUE_KEY"] = "agentdock:executions:test"
    os.environ["AGENT_QUEUE_KEY"] = "agentdock:agent_runs:test"
    os.environ["LLM_PROVIDER"] = "fake"
    get_settings.cache_clear()
    import app.core.config as config_mod

    config_mod.settings = get_settings()

    application = create_app()
    with TestClient(application) as client:
        engine = _sync_engine()
        with engine.begin() as conn:
            conn.execute(
                text(
                    "TRUNCATE agent_steps, agent_runs, execution_jobs, github_webhook_deliveries, "
                    "repository_connections, github_installations, github_accounts, auth_sessions, "
                    "agent_sessions, users RESTART IDENTITY CASCADE"
                )
            )
        engine.dispose()
        try:
            import redis as redis_sync

            redis_sync.from_url(get_settings().redis_url).flushdb()
        except Exception:
            pass
        yield client

    get_settings.cache_clear()


def _register(client: TestClient, email: str) -> None:
    response = client.post(
        "/api/auth/register",
        json={"email": email, "password": "password123", "display_name": "WS"},
    )
    assert response.status_code == 201, response.text


def _seed_connection(email: str) -> str:
    engine = _sync_engine()
    with Session(engine) as session:
        user = session.scalar(select(User).where(User.email == email))
        assert user is not None
        installation = GitHubInstallation(
            user_id=user.id,
            github_installation_id=int(uuid4().int % 10_000_000) + 9100,
            account_login="fixture",
            account_type="User",
            account_id=int(uuid4().int % 10_000_000) + 1,
            repository_selection="all",
        )
        session.add(installation)
        session.flush()
        connection = RepositoryConnection(
            user_id=user.id,
            installation_id=installation.id,
            github_repository_id=int(uuid4().int % 10_000_000) + 9200,
            owner="fixture",
            name="sample-repo",
            full_name="fixture/sample-repo",
            default_branch="main",
            private=False,
            html_url="https://github.com/fixture/sample-repo",
            is_active=True,
        )
        session.add(connection)
        session.commit()
        connection_id = str(connection.id)
    engine.dispose()
    return connection_id


def test_publish_event_sequence_and_serialization(ws_api: TestClient) -> None:
    run_id = uuid4()
    a = publish_agent_event_sync(run_id, "agent.run.queued", {"status": "queued"})
    b = publish_agent_event_sync(run_id, "agent.run.started", {"status": "running"})
    assert a is not None and b is not None
    assert a["sequence"] == 1
    assert b["sequence"] == 2
    assert a["version"] == 1
    assert "cookie" not in serialize_event(a).lower()
    assert publish_agent_event_sync(run_id, "agent.run.status", {"status": "running"})["sequence"] == 3


def test_publish_rejects_malformed(ws_api: TestClient) -> None:
    run_id = uuid4()
    assert publish_agent_event_sync(run_id, "", {"x": 1}) is None
    assert publish_agent_event_sync(run_id, "agent.run.status", "nope") is None  # type: ignore[arg-type]


def test_build_event_shape() -> None:
    payload = build_event(event="agent.ping", run_id=uuid4(), sequence=0, data={})
    assert set(payload) == {"version", "event", "run_id", "sequence", "timestamp", "data"}


def test_ws_anonymous_rejected(ws_api: TestClient) -> None:
    run_id = uuid4()
    with pytest.raises(Exception):
        with ws_api.websocket_connect(f"/ws/agent-runs/{run_id}"):
            pass


def test_ws_owner_connect_snapshot_and_idor(ws_api: TestClient) -> None:
    email_a = f"wsa-{uuid4().hex[:8]}@example.com"
    email_b = f"wsb-{uuid4().hex[:8]}@example.com"
    _register(ws_api, email_a)
    connection_id = _seed_connection(email_a)
    created = ws_api.post(
        "/api/agent-runs",
        json={"repository_connection_id": connection_id, "task": "Stream me"},
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["id"]

    with ws_api.websocket_connect(f"/ws/agent-runs/{run_id}") as ws:
        snap = ws.receive_json()
        assert snap["event"] == "agent.snapshot"
        assert snap["version"] == 1
        assert snap["data"]["status"] == "queued"
        assert "validation" in snap["data"]
        assert "publication_artifact_hash" in snap["data"]
        assert "approval_status" in snap["data"]
        assert "publication_status" in snap["data"]
        assert "approval_eligible" in snap["data"]
        assert settings.session_cookie_name not in str(snap)

        publish_agent_event_sync(run_id, "agent.run.started", {"status": "running"})
        seen = []
        for _ in range(20):
            msg = ws.receive_json()
            seen.append(msg["event"])
            if msg["event"] == "agent.run.started":
                assert msg["sequence"] >= 1
                break
        assert "agent.run.started" in seen

    ws_api.post("/api/auth/logout")
    _register(ws_api, email_b)
    with pytest.raises(Exception):
        with ws_api.websocket_connect(f"/ws/agent-runs/{run_id}"):
            pass


def test_ws_missing_run_and_revoked_session(ws_api: TestClient) -> None:
    email = f"wsr-{uuid4().hex[:8]}@example.com"
    _register(ws_api, email)
    connection_id = _seed_connection(email)
    created = ws_api.post(
        "/api/agent-runs",
        json={"repository_connection_id": connection_id, "task": "revoke"},
    )
    assert created.status_code == 201
    run_id = created.json()["id"]
    cookie = ws_api.cookies.get(settings.session_cookie_name)
    assert cookie

    with pytest.raises(Exception):
        with ws_api.websocket_connect(f"/ws/agent-runs/{uuid4()}"):
            pass

    ws_api.post("/api/auth/logout")
    ws_api.cookies.set(settings.session_cookie_name, cookie)
    with pytest.raises(Exception):
        with ws_api.websocket_connect(f"/ws/agent-runs/{run_id}"):
            pass


def test_ws_two_subscribers(ws_api: TestClient) -> None:
    email = f"wsm-{uuid4().hex[:8]}@example.com"
    _register(ws_api, email)
    connection_id = _seed_connection(email)
    created = ws_api.post(
        "/api/agent-runs",
        json={"repository_connection_id": connection_id, "task": "multi"},
    )
    run_id = created.json()["id"]

    with ws_api.websocket_connect(f"/ws/agent-runs/{run_id}") as ws1:
        with ws_api.websocket_connect(f"/ws/agent-runs/{run_id}") as ws2:
            ws1.receive_json()
            ws2.receive_json()
            publish_agent_event_sync(run_id, "agent.run.status", {"status": "running"})

            def wait_status(ws):
                for _ in range(20):
                    msg = ws.receive_json()
                    if msg["event"] == "agent.run.status":
                        return msg
                return None

            assert wait_status(ws1)["data"]["status"] == "running"
            assert wait_status(ws2)["data"]["status"] == "running"
