import jwt
import pytest
from fastapi import HTTPException

from app.config import Settings
from app.security import (
    authenticate,
    fingerprint_api_key,
    hash_api_key,
    validate_api_key,
    validate_optional_jwt,
)


def _settings(**updates) -> Settings:
    defaults = {
        "api_keys": ["test-api-key"],
        "jwt_secret": "test-secret-1234567890-abcdefghijklmnopqrstuvwxyz",
    }
    defaults.update(updates)
    return Settings(**defaults)


def test_validate_api_key_returns_context() -> None:
    context = validate_api_key("test-api-key", _settings())

    assert context.api_key_hash == hash_api_key("test-api-key")
    assert context.api_key_fingerprint == fingerprint_api_key("test-api-key")


def test_validate_api_key_rejects_invalid_key() -> None:
    with pytest.raises(HTTPException, match="Invalid API key"):
        validate_api_key("wrong-key", _settings())


def test_validate_optional_jwt_returns_subject() -> None:
    token = jwt.encode(
        {"sub": "user-123"},
        "test-secret-1234567890-abcdefghijklmnopqrstuvwxyz",
        algorithm="HS256",
    )

    subject = validate_optional_jwt(f"Bearer {token}", _settings())

    assert subject == "user-123"


def test_validate_optional_jwt_rejects_when_disabled() -> None:
    token = jwt.encode(
        {"sub": "user-123"},
        "test-secret-1234567890-abcdefghijklmnopqrstuvwxyz",
        algorithm="HS256",
    )

    with pytest.raises(HTTPException, match="JWT authentication is not enabled"):
        validate_optional_jwt(
            f"Bearer {token}",
            _settings(jwt_secret=None),
        )


def test_authenticate_attaches_optional_jwt_subject() -> None:
    token = jwt.encode(
        {"sub": "user-456"},
        "test-secret-1234567890-abcdefghijklmnopqrstuvwxyz",
        algorithm="HS256",
    )

    context = authenticate(
        _settings(),
        "test-api-key",
        f"Bearer {token}",
    )

    assert context.jwt_subject == "user-456"
