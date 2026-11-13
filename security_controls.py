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

_MUTATING_PREFIXES = ("/api/auth/", "/api/wallet/", "/api/payments/")
# Marketplace and offer routers already enforce authenticated, per-user action limits.
_EXEMPT = {"/api/telegram/webhook", "/health"}
_local: dict[str, deque[float]] = defaultdict(deque)
_redis = None


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
                return JSONResponse({"detail": "Request origin is not allowed"}, status_code=403)
            if not origin and request.headers.get("sec-fetch-site", "").lower() == "cross-site":
                return JSONResponse({"detail": "Cross-site mutation rejected"}, status_code=403)

        needs_edge_limit = path.startswith(_MUTATING_PREFIXES)
        if path not in _EXEMPT and needs_edge_limit:
            ip = request.client.host if request.client else "unknown"
            key = f"{method}:{path}:{ip}"
            limit, window = 60, 60
            redis_url = os.getenv("REDIS_URL", "").strip()
            if redis_url and Redis is not None:
                global _redis
                if _redis is None:
                    _redis = Redis.from_url(redis_url, decode_responses=True)
                try:
                    count = await _redis.incr(f"gift-radar:rl:{key}")
                    if count == 1:
                        await _redis.expire(f"gift-radar:rl:{key}", window)
                    if count > limit:
                        return JSONResponse(
                            {"detail": "Rate limit exceeded"},
                            status_code=429,
                            headers={"Retry-After": str(window)},
                        )
                except RedisError:
                    return JSONResponse(
                        {"detail": "Rate limiter unavailable; request rejected"},
                        status_code=503,
                    )
            else:
                if _is_production():
                    return JSONResponse(
                        {"detail": "Distributed rate limiter is required for production mutations"},
                        status_code=503,
                    )
                now = time.monotonic()
                hits = _local[key]
                while hits and hits[0] <= now - window:
                    hits.popleft()
                if len(hits) >= limit:
                    return JSONResponse(
                        {"detail": "Rate limit exceeded"},
                        status_code=429,
                        headers={"Retry-After": str(window)},
                    )
                hits.append(now)
        return await call_next(request)


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
