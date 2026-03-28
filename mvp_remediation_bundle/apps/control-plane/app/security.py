from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import jwt
from fastapi import Header, HTTPException, Request, WebSocket, status

from .config import Settings


@dataclass(slots=True)
class AuthContext:
    api_key_hash: str
    api_key_fingerprint: str
    jwt_subject: str | None = None


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def fingerprint_api_key(api_key: str) -> str:
    return hash_api_key(api_key)[:12]


def validate_api_key(api_key: str | None, settings: Settings) -> AuthContext:
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-API-Key header is required",
        )
    allowed = any(
        hmac.compare_digest(api_key, configured_key)
        for configured_key in settings.api_keys
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
    return AuthContext(
        api_key_hash=hash_api_key(api_key),
        api_key_fingerprint=fingerprint_api_key(api_key),
    )


def validate_optional_jwt(
    authorization: str | None, settings: Settings
) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header must contain a Bearer token",
        )
    if not settings.jwt_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="JWT authentication is not enabled",
        )
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid JWT: {exc}",
        ) from exc
    return str(claims.get("sub")) if claims.get("sub") else None


def authenticate(
    settings: Settings,
    api_key: str | None,
    authorization: str | None,
) -> AuthContext:
    context = validate_api_key(api_key, settings)
    context.jwt_subject = validate_optional_jwt(authorization, settings)
    return context


def get_auth_context(
    request: Request,
    api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> AuthContext:
    settings: Settings = request.app.state.settings
    return authenticate(settings, api_key, authorization)


def authenticate_websocket(websocket: WebSocket, settings: Settings) -> AuthContext:
    api_key = websocket.headers.get("x-api-key") or websocket.query_params.get("api_key")
    authorization = websocket.headers.get("authorization")
    return authenticate(settings, api_key, authorization)


def build_signed_url(
    raw_url: str,
    *,
    secret: str,
    ttl_seconds: int,
) -> tuple[str, datetime]:
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    expires_ts = int(expires_at.timestamp())
    signature = hmac.new(
        secret.encode("utf-8"),
        msg=f"{raw_url}:{expires_ts}".encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

    parsed = urlsplit(raw_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["expires"] = str(expires_ts)
    query["signature"] = signature
    signed_url = urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(query),
            parsed.fragment,
        )
    )
    return signed_url, expires_at

