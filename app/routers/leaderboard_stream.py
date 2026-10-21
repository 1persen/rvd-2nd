"""
Leaderboard SSE stream — Server-Sent Events, bukan WebSocket.
Klien subscribe, server push update setiap kali vote sukses diproses.

Per client this costs one small queue and this generator: no Redis connection
(see cache.subscription), no extra tasks, and no DB connection after the first
message (the initial ranking comes from the shared cache).
"""
import asyncio
import logging
import random
from typing import AsyncGenerator

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.core.database import async_session
from app.services import cache, leaderboard_service
from app.utils import dumps

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["leaderboard"])

KEEPALIVE_SECONDS = 20


def _encode_sse(data: dict) -> str:
    return f"data: {dumps(data)}\n\n"


@router.get("/leaderboard/stream")
async def leaderboard_stream(
    event_id: str = Query(..., description="ID event"),
    level: str | None = Query(default=None),
):
    async def event_source() -> AsyncGenerator[str, None]:
        yield f"retry: {random.randint(2000, 7000)}\n\n"
        async with cache.subscription(event_id) as queue:
            initial = await _load_initial(event_id, level)
            if initial:
                yield _encode_sse({"type": "leaderboard", "event_id": event_id, "level": level, "data": initial})
            while True:
                try:
                    frame = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
                    yield frame
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _load_initial(event_id: str, level: str | None = None):
    try:
        async with async_session() as session:
            return await leaderboard_service.get_leaderboard(session, event_id, level=level)
    except Exception as exc:
        logger.warning("SSE initial leaderboard failed for %s: %s", event_id, exc)
        return None
