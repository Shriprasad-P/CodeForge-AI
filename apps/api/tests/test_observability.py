from __future__ import annotations

import pytest

from app.core.observability import (
    CANONICAL_OBSERVABILITY_FIELDS,
    claim_ref,
    normalize_request_id,
    safe_error,
    validate_observability_fields,
)


def test_canonical_observability_vocabulary_contains_workflow_boundaries() -> None:
    required = {
        "request_id",
        "workflow_correlation_id",
        "agent_run_id",
        "execution_job_id",
        "repository_connection_id",
        "outbox_event_id",
        "delivery_attempt",
        "claim_ref",
        "worker_id",
        "publication_attempt_id",
        "event",
        "duration_ms",
        "error_class",
        "retryable",
        "state_from",
        "state_to",
    }
    assert required <= CANONICAL_OBSERVABILITY_FIELDS
    validate_observability_fields({key: "value" for key in required})
    with pytest.raises(ValueError):
        validate_observability_fields({"request_id": "value", "renamed_request": "value"})


def test_request_ids_are_bounded_and_claim_refs_are_non_reversible() -> None:
    request_id = "request-123"
    assert normalize_request_id(request_id) == request_id
    assert normalize_request_id("\n" + "x" * 300) != "x" * 300
    token = "sensitive-claim-token"
    ref = claim_ref(token)
    assert ref is not None and len(ref) == 12
    assert token not in ref
    assert claim_ref(token) == ref


def test_safe_error_redacts_credentials_and_private_keys() -> None:
    text = "password=super-secret token=abc123 -----BEGIN PRIVATE KEY-----secret-----END PRIVATE KEY-----"
    redacted = safe_error(text)
    assert "super-secret" not in redacted
    assert "abc123" not in redacted
    assert "BEGIN PRIVATE KEY" not in redacted
