from app.auth.security import hash_password, hash_session_token, normalize_email, verify_password


def test_password_hash_roundtrip() -> None:
    hashed = hash_password("password123")
    assert hashed != "password123"
    assert verify_password(hashed, "password123")
    assert not verify_password(hashed, "wrong")


def test_normalize_email() -> None:
    assert normalize_email("  Ada@Example.COM ") == "ada@example.com"


def test_session_token_hash_is_sha256_hex() -> None:
    digest = hash_session_token("abc")
    assert len(digest) == 64
    assert digest == hash_session_token("abc")
    assert digest != hash_session_token("abcd")
