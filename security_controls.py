"""Origin checks and API rate limits. Redis is the shared production limiter."""
from __future__ import annotations
import os, time
from collections import defaultdict, deque
from urllib.parse import urlsplit
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

try:
    from redis.asyncio import Redis
except ImportError:
    Redis = None

_MUTATING_PREFIXES = ("/api/auth/", "/api/marketplace/", "/api/demo-offers/", "/api/wallet/", "/api/payments/")
_EXEMPT = {"/api/telegram/webhook", "/health"}
_local: dict[str, deque[float]] = defaultdict(deque)
_redis = None

def _origins() -> set[str]:
    raw = os.getenv("CORS_ALLOWED_ORIGINS", "https://nft-gift-scanner.fj7kmzkhn9.workers.dev,https://nft-gift-scanner.pages.dev")
    return {x.strip().rstrip("/") for x in raw.split(",") if x.strip()}

def _valid_origin(origin: str) -> bool:
    try:
        p = urlsplit(origin)
        return p.scheme == "https" and bool(p.netloc) and origin.rstrip("/") == f"{p.scheme}://{p.netloc}"
    except ValueError:
        return False

class SecurityControlsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path, method = request.url.path, request.method.upper()
        if path not in _EXEMPT and method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and (not _valid_origin(origin) or origin.rstrip("/") not in _origins()):
                return JSONResponse({"detail":"Request origin is not allowed"}, status_code=403)
            if not origin and request.headers.get("sec-fetch-site", "").lower() == "cross-site":
                return JSONResponse({"detail":"Cross-site mutation rejected"}, status_code=403)
        if path not in _EXEMPT and (path.startswith(_MUTATING_PREFIXES) or method not in {"GET","HEAD","OPTIONS"}):
            ip = request.client.host if request.client else "unknown"
            key = f"{method}:{path}:{ip}"
            limit, window = (30, 60)
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
                        return JSONResponse({"detail":"Rate limit exceeded"}, status_code=429, headers={"Retry-After":str(window)})
                except Exception:
                    return JSONResponse({"detail":"Rate limiter unavailable; request rejected"}, status_code=503)
            else:
                if os.getenv("ENVIRONMENT", "").lower() == "production":
                    return JSONResponse({"detail": "Distributed rate limiter is required for production mutations"}, status_code=503)
                now = time.monotonic()
                hits = _local[key]
                while hits and hits[0] <= now-window:
                    hits.popleft()
                if len(hits) >= limit:
                    return JSONResponse({"detail":"Rate limit exceeded"}, status_code=429, headers={"Retry-After":str(window)})
                hits.append(now)
        return await call_next(request)

def rate_limit_status() -> dict[str, object]:
    distributed = bool(os.getenv("REDIS_URL", "").strip() and Redis is not None)
    return {"enabled": True, "backend": "redis" if distributed else "process-local",
            "distributed": distributed, "production_ready": distributed}
