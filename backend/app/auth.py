import json
import os
import secrets
from urllib.parse import urlparse
from pathlib import Path
from typing import Callable

from fastapi import WebSocket
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

_AUTH_TOKEN_CACHE: str | None = None
_TOKEN_ENV_VAR = "JUSTSEARCH_AUTH_TOKEN"
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_PROTECTED_HTTP_PREFIXES = ("/api",)


def get_auth_token_path() -> Path:
    return Path(__file__).resolve().parents[1] / ".auth_token"


def is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    normalized = host.strip().lower()
    if normalized in _LOOPBACK_HOSTS:
        return True
    if normalized.startswith("::ffff:"):
        normalized = normalized[7:]
    return normalized == "127.0.0.1"


def get_auth_token() -> str:
    global _AUTH_TOKEN_CACHE

    env_token = os.getenv(_TOKEN_ENV_VAR, "").strip()
    if env_token:
        _AUTH_TOKEN_CACHE = env_token
        return env_token

    if _AUTH_TOKEN_CACHE:
        return _AUTH_TOKEN_CACHE

    token_path = get_auth_token_path()
    token_path.parent.mkdir(parents=True, exist_ok=True)

    if token_path.exists():
        existing = token_path.read_text(encoding="utf-8").strip()
        if existing:
            _AUTH_TOKEN_CACHE = existing
            return existing

    token = secrets.token_urlsafe(32)
    token_path.write_text(token, encoding="utf-8")
    try:
        os.chmod(token_path, 0o600)
    except OSError:
        pass

    _AUTH_TOKEN_CACHE = token
    return token


def get_bearer_token(headers) -> str:
    auth_header = headers.get("authorization", "").strip()
    if not auth_header.lower().startswith("bearer "):
        return ""
    return auth_header.split(" ", 1)[1].strip()


def get_request_token(request: Request) -> str:
    return get_bearer_token(request.headers) or request.query_params.get("token", "").strip()


def is_trusted_loopback_origin(origin: str | None) -> bool:
    if not origin:
        return True

    parsed = urlparse(origin)
    return is_loopback_host(parsed.hostname)


def is_http_request_authorized(
    request: Request,
    token_provider: Callable[[], str] = get_auth_token,
) -> bool:
    client_host = request.client.host if request.client else None
    if is_loopback_host(client_host):
        return is_trusted_loopback_origin(request.headers.get("origin"))

    expected = token_provider()
    provided = get_request_token(request)
    return bool(provided) and secrets.compare_digest(provided, expected)


async def authorize_websocket(
    websocket: WebSocket,
    token_provider: Callable[[], str] = get_auth_token,
) -> bool:
    client_host = websocket.client.host if websocket.client else None
    if is_loopback_host(client_host):
        return is_trusted_loopback_origin(websocket.headers.get("origin"))

    expected = token_provider()
    provided = get_bearer_token(websocket.headers) or websocket.query_params.get("token", "").strip()
    if provided and secrets.compare_digest(provided, expected):
        return True

    await websocket.close(code=4401, reason="Unauthorized")
    return False


def build_html_bootstrap_payload(request: Request) -> dict:
    client_host = request.client.host if request.client else None
    loopback_client = is_loopback_host(client_host)
    payload = {
        "authEnabled": True,
        "clientIsLoopback": loopback_client,
    }
    if loopback_client:
        payload["authToken"] = get_auth_token()
    return payload


def inject_html_bootstrap(html: str, payload: dict) -> str:
    script = (
        "<script>"
        f"window.__JUSTSEARCH_BOOTSTRAP__ = {json.dumps(payload, ensure_ascii=False)};"
        "</script>"
    )
    if "</head>" in html:
        return html.replace("</head>", f"{script}\n</head>", 1)
    return f"{script}\n{html}"


class AccessControlMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        token_provider: Callable[[], str] = get_auth_token,
        protected_prefixes: tuple[str, ...] = _PROTECTED_HTTP_PREFIXES,
    ):
        super().__init__(app)
        self.token_provider = token_provider
        self.protected_prefixes = protected_prefixes

    def _is_protected_path(self, path: str) -> bool:
        for prefix in self.protected_prefixes:
            if path == prefix or path.startswith(f"{prefix}/"):
                return True
        return False

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS" or not self._is_protected_path(request.url.path):
            return await call_next(request)

        if is_http_request_authorized(request, self.token_provider):
            return await call_next(request)

        return JSONResponse(
            {"detail": "Unauthorized. Provide a valid Bearer token."},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
