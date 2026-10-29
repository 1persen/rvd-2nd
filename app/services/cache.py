"""
Leaderboard cache + SSE fan-out.

Production: Redis pub/sub + cache.
Dev/test fallback (REDIS_URL=memory://...): in-memory dict, single process.

Fan-out: each worker process holds ONE Redis subscription and copies every
leaderboard message into the queues of its own SSE clients, so 200 viewers cost
one Redis connection per worker instead of one each. Messages are parsed and
framed once per process, not once per client.
SSE memakai pub/sub broadcast — bukan WebSocket.
"""
import asyncio
import json
import logging
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

from redis.asyncio import Redis, from_url

from app.core.config import settings
from app.utils import dumps

logger = logging.getLogger(__name__)

_CHANNEL = "wvc:leaderboard"

_redis: Optional[Redis] = None
_memory_cache: dict[str, tuple[float, str]] = {}
_memory_locks: dict[str, float] = {}

# (event_id, level) -> queues of this process's SSE clients (each holds SSE frames).
# The level keeps a category leaderboard from receiving the event-wide ranking.
_subscribers: dict[tuple[str, Optional[str]], set[asyncio.Queue]] = defaultdict(set)
_listener: Optional[asyncio.Task] = None

CACHE_TTL = 10  # detik


def _use_redis() -> bool:
    return settings.is_redis_available


async def _get_redis() -> Optional[Redis]:
    global _redis
    if not _use_redis():
        return None
    if _redis is None:
        _redis = from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _redis


# --- Cache ----------------------------------------------------------------
async def cache_get(key: str) -> Optional[str]:
    if not _use_redis():
        item = _memory_cache.get(key)
        if item and item[0] > time.time():
            return item[1]
        return None
    try:
        return await (await _get_redis()).get(key)
    except Exception as exc:
        logger.warning("cache_get failed for %s: %s", key, exc)
        return None


async def cache_set(key: str, value: str, ttl: int = CACHE_TTL) -> None:
    if not _use_redis():
        _memory_cache[key] = (time.time() + ttl, value)
        return
    try:
        await (await _get_redis()).set(key, value, ex=ttl)
    except Exception as exc:
        logger.warning("cache_set failed for %s: %s", key, exc)


async def try_lock(key: str, ttl_seconds: float) -> bool:
    """True for the first caller within ttl_seconds, across all workers."""
    if not _use_redis():
        now = time.monotonic()
        if _memory_locks.get(key, 0) > now:
            return False
        _memory_locks[key] = now + ttl_seconds
        return True
    try:
        return bool(await (await _get_redis()).set(key, "1", nx=True, px=int(ttl_seconds * 1000)))
    except Exception:
        logger.warning("try_lock failed for %s", key, exc_info=True)
        return True  # fail open: an extra push is harmless, a lost one is not


# --- Pub/sub broadcast ------------------------------------------------------
def _deliver(queue: asyncio.Queue, frame: str) -> None:
    if queue.full():
        # A slow client only needs the newest message; drop the stale one.
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
    queue.put_nowait(frame)


def _fan_out(raw: str) -> None:
    """Deliver one published message to this process's SSE clients of its event+level."""
    try:
        message = json.loads(raw)
    except (TypeError, ValueError):
        return
    if not isinstance(message, dict):
        return
    frame = f"data: {raw}\n\n"
    if message.get("type") == "event_visibility":
        # Visibility affects every level stream of that event, so close all of
        # them instead of only the default (level=None) stream.
        for (event_id, _level), queues in list(_subscribers.items()):
            if event_id == message.get("event_id"):
                for queue in list(queues):
                    _deliver(queue, frame)
        return
    if message.get("type") == "support":
        # Public support events are event-wide; only the default stream carries them.
        for queue in list(_subscribers.get((message.get("event_id"), None), ())):
            _deliver(queue, frame)
        return
    if message.get("type") != "leaderboard":
        return
    key = (message.get("event_id"), message.get("level"))
    for queue in list(_subscribers.get(key, ())):
        _deliver(queue, frame)


async def _listen_forever() -> None:
    """The single Redis subscription of this process; reconnects with capped backoff."""
    backoff = 1.0
    while True:
        client = from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_keepalive=True,
            health_check_interval=30,
        )
        pubsub = client.pubsub(ignore_subscribe_messages=True)
        try:
            await pubsub.subscribe(_CHANNEL)
            backoff = 1.0
            async for raw in pubsub.listen():
                if raw.get("type") == "message":
                    _fan_out(raw["data"])
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Redis subscription lost; retrying in %.0fs", backoff, exc_info=True)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
        finally:
            try:
                await pubsub.aclose()
                await client.aclose()
            except Exception:
                pass


def _ensure_listener() -> None:
    global _listener
    if _use_redis() and (_listener is None or _listener.done()):
        _listener = asyncio.create_task(_listen_forever())


@asynccontextmanager
async def subscription(
    event_id: str, level: Optional[str] = None, maxsize: int = 4
) -> AsyncIterator[asyncio.Queue]:
    """Queue of ready-to-send SSE frames for one client of one event and category."""
    _ensure_listener()
    key = (event_id, level)
    queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
    _subscribers[key].add(queue)
    try:
        yield queue
    finally:
        subs = _subscribers.get(key)
        if subs is not None:
            subs.discard(queue)
            if not subs:
                _subscribers.pop(key, None)


def subscriber_count() -> int:
    return sum(len(s) for s in _subscribers.values())


async def publish(payload: dict[str, Any]) -> None:
    message = dumps(payload)
    if not _use_redis():
        _fan_out(message)
        return
    try:
        await (await _get_redis()).publish(_CHANNEL, message)
    except Exception:
        logger.warning("publish failed", exc_info=True)


async def publish_event_visibility(event_id: str) -> None:
    """Tell every SSE stream of this event to close (the event stopped being public)."""
    await publish({"type": "event_visibility", "event_id": event_id, "level": None})


def _leaderboard_key(event_id: str, level: str | None = None) -> str:
    if level:
        return f"wvc:leaderboard:{event_id}:{level}"
    return f"wvc:leaderboard:{event_id}:all"


async def get_cached_leaderboard(event_id: str, level: str | None = None) -> Optional[dict]:
    raw = await cache_get(_leaderboard_key(event_id, level))
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


async def set_cached_leaderboard(event_id: str, payload: dict, level: str | None = None) -> None:
    await cache_set(_leaderboard_key(event_id, level), dumps(payload))
