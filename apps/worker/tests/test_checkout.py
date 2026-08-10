from __future__ import annotations

from src.checkout import sanitize_text


def test_sanitize_token() -> None:
    text = "https://x-access-token:ghs_secret@github.com/o/r.git Authorization: Bearer abc"
    out = sanitize_text(text, token="ghs_secret")
    assert "ghs_secret" not in out
    assert "[REDACTED]" in out
