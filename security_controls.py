"""Origin checks and API rate limits. Redis is the shared production limiter."""
from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

try:
    from redis.asyncio import Redis
    from redis.exceptions import RedisError
except ImportError:
    Redis = None
    RedisError = ConnectionError

# Every API route is rate-limited by IP. Authenticated mutation handlers may
# additionally apply per-account action limits in their domain services.
_EXEMPT = {"/health"}
_local: dict[str, deque[float]] = defaultdict(deque)
_redis = None
_RATE_LIMIT_SCRIPT = (
    "local count = redis.call('INCR', KEYS[1]); "
    "if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]); end; "
    "return count"
)
API_MAX_BODY_BYTES = 1_048_576


def _requires_edge_limit(path: str) -> bool:
    return path.startswith("/api/") and path not in _EXEMPT


def _apply_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
    if response.headers.get("content-type", "").lower().startswith("application/json"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def _origins() -> set[str]:
    raw = os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "https://nft-gift-scanner.fj7kmzkhn9.workers.dev,https://nft-gift-scanner.pages.dev",
    )
    return {value.strip().rstrip("/") for value in raw.split(",") if value.strip()}


def _valid_origin(origin: str) -> bool:
    """Accept only canonical HTTPS origins explicitly present in the allowlist."""
    try:
        parsed = urlsplit(origin)
        canonical = f"{parsed.scheme}://{parsed.netloc}"
        return (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
            and not parsed.path
            and not parsed.query
            and not parsed.fragment
            and origin.rstrip("/") == canonical
            and canonical in _origins()
        )
    except (ValueError, UnicodeError):
        return False


class SecurityControlsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path, method = request.url.path, request.method.upper()
        is_mutation = method not in {"GET", "HEAD", "OPTIONS"}
        if path not in _EXEMPT and is_mutation:
            origin = request.headers.get("origin")
            if origin and not _valid_origin(origin):
                return _apply_security_headers(JSONResponse({"detail": "Request origin is not allowed"}, status_code=403))
            if not origin and request.headers.get("sec-fetch-site", "").lower() == "cross-site":
                return _apply_security_headers(JSONResponse({"detail": "Cross-site mutation rejected"}, status_code=403))

        needs_edge_limit = _requires_edge_limit(path)
        if needs_edge_limit:
            ip = request.client.host if request.client else "unknown"
            route_parts = path.strip("/").split("/")
            route_bucket = "/" + "/".join(route_parts[:2])
            key = f"{method}:{route_bucket}:{ip}"
            # Public reads receive a larger budget; mutations and the signed
            # Telegram webhook have separate budgets to preserve retry headroom.
            if path == "/api/telegram/webhook":
                limit = 300
            elif route_bucket == "/api/marketplace" and is_mutation:
                # Domain routes also enforce per-account action quotas.
                limit = 300
            else:
                limit = 60 if is_mutation else 120
            window = 60
            redis_url = os.getenv("REDIS_URL", "").strip()
            if redis_url and Redis is not None:
                global _redis
                if _redis is None:
                    _redis = Redis.from_url(redis_url, decode_responses=True)
                try:
                    count = await _redis.eval(
                        _RATE_LIMIT_SCRIPT, 1, f"gift-radar:rl:{key}", window
                    )
                    if count > limit:
                        return _apply_security_headers(JSONResponse(
                            {"detail": "Rate limit exceeded"},
                            status_code=429,
                            headers={"Retry-After": str(window)},
                        ))
                except RedisError:
                    return _apply_security_headers(JSONResponse(
                        {"detail": "Rate limiter unavailable; request rejected"},
                        status_code=503,
                    ))
            else:
                if _is_production():
                    return _apply_security_headers(JSONResponse(
                        {"detail": "Distributed rate limiter is required for production API traffic"},
                        status_code=503,
                    ))
                now = time.monotonic()
                hits = _local[key]
                while hits and hits[0] <= now - window:
                    hits.popleft()
                if len(hits) >= limit:
                    return _apply_security_headers(JSONResponse(
                        {"detail": "Rate limit exceeded"},
                        status_code=429,
                        headers={"Retry-After": str(window)},
                    ))
                hits.append(now)
        response = await call_next(request)
        return _apply_security_headers(response)


class RequestBodyLimitMiddleware:
    """Bound all API request bodies before FastAPI/Pydantic buffers them."""

    def __init__(self, app, max_body_bytes: int = API_MAX_BODY_BYTES):
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope, receive, send):
        if (
            scope.get("type") != "http"
            or not scope.get("path", "").startswith("/api/")
            or scope.get("method", "GET").upper() not in {"POST", "PUT", "PATCH", "DELETE"}
        ):
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        raw_length = headers.get(b"content-length")
        if raw_length is not None:
            try:
                declared_length = int(raw_length)
            except (TypeError, ValueError):
                response = _apply_security_headers(JSONResponse(
                    {"detail": "Invalid Content-Length"}, status_code=400
                ))
                await response(scope, receive, send)
                return
            if declared_length < 0:
                response = _apply_security_headers(JSONResponse(
                    {"detail": "Invalid Content-Length"}, status_code=400
                ))
                await response(scope, receive, send)
                return
            if declared_length > self.max_body_bytes:
                response = _apply_security_headers(JSONResponse(
                    {"detail": "Request body too large"}, status_code=413
                ))
                await response(scope, receive, send)
                return

        buffered_messages = []
        received_bytes = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            received_bytes += len(message.get("body", b""))
            if received_bytes > self.max_body_bytes:
                response = _apply_security_headers(JSONResponse(
                    {"detail": "Request body too large"}, status_code=413
                ))
                await response(scope, receive, send)
                return
            buffered_messages.append(message)
            if not message.get("more_body", False):
                break

        async def replay_receive():
            if buffered_messages:
                return buffered_messages.pop(0)
            return await receive()

        await self.app(scope, replay_receive, send)


def _is_production() -> bool:
    value = os.getenv("ENVIRONMENT", os.getenv("RAILWAY_ENVIRONMENT_NAME", "")).lower()
    return value == "production"


def rate_limit_status() -> dict[str, object]:
    distributed = bool(os.getenv("REDIS_URL", "").strip() and Redis is not None)
    return {
        "enabled": True,
        "backend": "redis" if distributed else "process-local",
        "distributed": distributed,
        "production_ready": distributed,
    }
