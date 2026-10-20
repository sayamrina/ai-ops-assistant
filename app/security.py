"""Authentication (API keys with roles) and webhook signature checks."""
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request, status

from .config import Settings, get_settings

# Higher roles include the permissions of lower ones
ROLE_LEVEL = {"viewer": 1, "operator": 2, "reviewer": 3, "admin": 4}


@dataclass(frozen=True)
class Principal:
    name: str
    role: str


def hash_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


def new_api_key() -> str:
    return "ops_" + secrets.token_urlsafe(24)


def _known_keys(settings: Settings) -> dict[str, dict]:
    try:
        return json.loads(settings.api_keys)
    except json.JSONDecodeError as exc:  # misconfiguration must not open the API
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "API key configuration is invalid") from exc


def authenticate(
    x_api_key: str | None = Header(default=None), settings: Settings = Depends(get_settings)
) -> Principal:
    if not x_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing X-API-Key header")
    digest = hash_key(x_api_key)
    for known, info in _known_keys(settings).items():
        if hmac.compare_digest(known, digest):
            return Principal(name=info["name"], role=info["role"])
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API key")


def require_role(minimum: str):
    def checker(principal: Principal = Depends(authenticate)) -> Principal:
        if ROLE_LEVEL.get(principal.role, 0) < ROLE_LEVEL[minimum]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Role '{minimum}' or higher required")
        return principal

    return checker


def sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def verify_webhook_signature(request: Request, settings: Settings = Depends(get_settings)) -> bytes:
    """Checks the X-Hub-Signature header (format used by Jira Cloud webhooks: sha256=<hex>)."""
    if settings.webhook_secret is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Webhook secret is not configured")
    body = await request.body()
    received = request.headers.get("X-Hub-Signature", "")
    expected = sign(body, settings.webhook_secret.get_secret_value())
    if not hmac.compare_digest(received, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid webhook signature")
    return body
