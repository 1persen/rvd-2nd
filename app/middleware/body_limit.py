"""Bounded request-body reader for admin event Save/Preview.

Multipart parsing happens inside FastAPI/Starlette, so the size check must run
before the parser sees the body. This buffers at most the limit plus one chunk
for the two admin endpoints that accept files, and answers 413 without invoking
the route.
"""
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.services.event_asset_service import MAX_TOTAL_BODY
from app.services.team_import_service import MAX_FILE_BYTES, is_import_path

# 1 MiB file plus multipart overhead (boundary, headers, sha256 field).
IMPORT_TOTAL_BODY = MAX_FILE_BYTES + 64 * 1024


def _import_limited(scope: Scope) -> bool:
    return is_import_path(scope.get("method", ""), scope.get("path", ""))


def _is_limited(scope: Scope) -> bool:
    if scope["type"] != "http":
        return False
    path = scope.get("path", "")
    method = scope.get("method", "")
    return path == "/api/v1/admin/events/preview" or (
        method == "PUT" and path.startswith("/api/v1/admin/events/")
    )


async def _send_413(send: Send, message: str) -> None:
    body = f'{{"success":false,"message":"{message}","data":null}}'.encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": 413,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ],
    })
    await send({"type": "http.response.body", "body": body})


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if _import_limited(scope):
            limit = IMPORT_TOTAL_BODY
            limit_message = "Ukuran unggahan melebihi 1 MiB"
        elif _is_limited(scope):
            limit = MAX_TOTAL_BODY
            limit_message = "Ukuran unggahan melebihi 17 MiB"
        else:
            await self.app(scope, receive, send)
            return

        total = 0
        chunks: list[bytes] = []
        while True:
            message: Message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > limit:
                await _send_413(send, limit_message)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break

        body = b"".join(chunks)
        replayed = False

        async def replay_receive() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        await self.app(scope, replay_receive, send)
