"""
Audit middleware — mencatat aksi sensitif (mutasi) secara append-only ke audit_logs.
Hanya mencatat request yang mengubah state (POST/PUT/PATCH/DELETE) di bawah /api/v1.
Menggunakan session DB sendiri agar tidak mengganggu transaksi request utama.

Pure ASGI: the row is written after the response has been sent, so the client
never waits for the audit insert.
"""
import logging

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.database import async_session
from app.core.jwt import decode_access_token
from app.enums.audit_actor import AuditActor
from app.repositories import audit_log as audit_repo

logger = logging.getLogger(__name__)

_SKIP_PATHS = ("/api/v1/payments/webhook", "/api/v1/payments/duitku/callback")
_MUTATING = ("POST", "PUT", "PATCH", "DELETE")


class AuditMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] not in _MUTATING
            or not scope["path"].startswith("/api/v1/")
            or scope["path"] in _SKIP_PATHS
        ):
            await self.app(scope, receive, send)
            return

        status = 500

        async def send_capture(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_capture)
        finally:
            await _write(scope, status)


async def _write(scope: Scope, status: int) -> None:
    headers = Headers(scope=scope)
    path = scope["path"]
    actor_type = None
    actor_name = None
    auth_header = headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        payload = decode_access_token(auth_header.removeprefix("Bearer ").strip())
        if payload:
            actor_type = AuditActor.ADMIN
            actor_name = payload.get("email")

    client = scope.get("client")
    parts = path.split("/")
    try:
        async with async_session() as session:
            await audit_repo.create_audit_log(
                session,
                action=f"{scope['method']} {path}",
                actor_type=actor_type,
                actor_name=actor_name,
                request_id=None,
                entity_type=parts[2] if len(parts) > 3 else None,
                detail={"status_code": status},
                ip_address=client[0] if client else None,
                user_agent=headers.get("user-agent"),
            )
            await session.commit()
    except Exception:
        logger.warning("Audit log write failed for %s %s", scope["method"], path, exc_info=True)
