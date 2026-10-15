"""
Rate limiter middleware (pure ASGI) — fixed 60 s window per client IP and bucket.

Production: Redis (INCR + EXPIRE NX in one round trip; needs Redis >= 7).
Dev/test fallback: in-memory sliding window (single-process only).

The client IP is scope["client"], which uvicorn rewrites from X-Forwarded-For
only for trusted proxies (--forwarded-allow-ips). Behind Caddy on the same host
that is 127.0.0.1, the uvicorn default; without it every voter shares Caddy's IP.
"""
import logging
import time
from collections import defaultdict, deque
from typing import Deque

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings
from app.services import cache

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60
_EXEMPT_PREFIXES = ("/static/", "/health")


def _limit(bucket: str) -> int:
    return {
        "public": settings.RATE_LIMIT_PUBLIC_PER_MIN,
        "payment": settings.RATE_LIMIT_PAYMENT_PER_MIN,  # invoice creation only
        "webhook": settings.RATE_LIMIT_WEBHOOK_PER_MIN,
        "auth": settings.RATE_LIMIT_AUTH_PER_MIN,
    }[bucket]


def _bucket_name(method: str, path: str) -> str:
    if path.startswith("/api/v1/auth"):
        return "auth"
    if path in ("/api/v1/payments/webhook", "/api/v1/payments/duitku/callback"):
        return "webhook"
    if method == "POST" and path == "/api/v1/payments":
        return "payment"
    # Status polling (GET /payments/{id}/status) is ordinary public traffic.
    return "public"


class InMemoryLimiter:
    """Sliding window counter — safe for single-process dev/test."""

    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], Deque[float]] = defaultdict(deque)

    def allow(self, key: str, bucket: str) -> bool:
        limit = _limit(bucket)
        now = time.monotonic()
        history = self._hits[(key, bucket)]
        while history and history[0] <= now - WINDOW_SECONDS:
            history.popleft()
        if len(history) >= limit:
            return False
        history.append(now)
        return True


_memory_limiter = InMemoryLimiter()


async def _redis_allow(key: str, bucket: str) -> bool:
    redis_key = f"wvc:ratelimit:{bucket}:{key}"
    try:
        r = await cache._get_redis()
        async with r.pipeline(transaction=False) as pipe:
            pipe.incr(redis_key)
            pipe.expire(redis_key, WINDOW_SECONDS, nx=True)
            current, _ = await pipe.execute()
        return current <= _limit(bucket)
    except Exception as exc:
        # Jangan blokir traffic saat Redis bermasalah. One line, no traceback:
        # this runs on every request while Redis is down.
        logger.warning("Rate limiter Redis error; allowing request: %s", exc)
        return True


_TOO_MANY = JSONResponse(
    status_code=429,
    content={
        "success": False,
        "message": "Terlalu banyak permintaan. Silakan coba beberapa saat lagi.",
        "data": None,
    },
    headers={"Retry-After": str(WINDOW_SECONDS)},
)


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or path.startswith(_EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        client_ip = client[0] if client else "unknown"
        bucket = _bucket_name(scope["method"], path)

        if settings.is_redis_available:
            allowed = await _redis_allow(client_ip, bucket)
        else:
            allowed = _memory_limiter.allow(client_ip, bucket)

        if not allowed:
            await _TOO_MANY(scope, receive, send)
            return
        await self.app(scope, receive, send)
