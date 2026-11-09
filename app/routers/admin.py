"""
Admin API — dashboard stats, payments, audit logs, Excel export.
Semua endpoint admin butuh JWT authentication.

Legacy session-based auth is deprecated but still available at /api/v1/auth/*.
"""
from datetime import datetime, timezone
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.enums.payment_gateway import PaymentGateway
from app.enums.payment_status import PaymentStatus
from app.repositories import audit_log as audit_repo
from app.repositories import event as event_repo
from app.repositories import payment as payment_repo
from app.repositories import team as team_repo
from app.repositories import user as user_repo
from app.repositories import vote_log as vote_log_repo
from app.routers.deps import get_current_admin_jwt
from app.schemas.common import ok
from app.services import audit_service
from app.services import export_service
from app.services import payment_service
from app.services.vote_service import process_successful_payment
from app.services import payment_service
from app.services.vote_service import process_successful_payment

router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin"],
    dependencies=[Depends(get_current_admin_jwt)],
)


@router.get("/dashboard")
async def dashboard(
    event_id: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
):
    events = await event_repo.list_events(session, limit=100)
    if not events:
        return ok({"events": 0, "teams": 0, "payments": 0, "votes": 0, "users": 0})

    target_id = event_id or (events[0].id if events else None)
    # One GROUP BY instead of loading every payment row (with JSON payloads).
    summary = await payment_repo.status_summary(session, target_id)

    def count(status: PaymentStatus) -> int:
        return summary.get(status, (0, 0))[0]

    success_count = count(PaymentStatus.SUCCESS)
    total_amount = summary.get(PaymentStatus.SUCCESS, (0, 0))[1]
    total_votes = await vote_log_repo.get_total_votes(session, event_id=target_id)
    users = await user_repo.list_users(session)

    target_event = await event_repo.get_event(session, target_id) if target_id else None

    return ok(
        {
            "events": len(events),
            "teams": len(await team_repo.list_teams(session, target_id)) if target_id else 0,
            "payments": sum(c for c, _ in summary.values()),
            "successful_payments": success_count,
            "total_amount": total_amount,
            "votes": total_votes,
            "users": len(users),
            "current_event_id": target_id,
            "event_name": target_event.name if target_event else None,
            "event_status": target_event.status.value if target_event else None,
            "pending_payments": count(PaymentStatus.PENDING),
            "failed_payments": count(PaymentStatus.FAILED),
            "expired_payments": count(PaymentStatus.EXPIRED),
            "canceled_payments": count(PaymentStatus.CANCELED),
        }
    )


@router.get("/payments")
async def list_payments(
    event_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    inquiry_state: str | None = Query(default=None),
    needs_review: bool | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
):
    status_enum = PaymentStatus(status) if status else None
    payments = await payment_repo.list_payments(session, event_id=event_id, status=status_enum, limit=500)
    result = []
    for payment in payments:
        duitku_payload = payment.duitku_payload or {}
        # Filter JSON dilakukan di Python (bukan SQL) agar dialect-safe.
        if inquiry_state is not None and duitku_payload.get("_inquiry_state") != inquiry_state:
            continue
        if needs_review is not None and bool(duitku_payload.get("_needs_manual_review")) != needs_review:
            continue
        team = await team_repo.get_team(session, payment.team_id)
        event = await event_repo.get_event(session, payment.event_id)
        result.append(
            {
                "id": payment.id,
                "event_id": payment.event_id,
                "event_name": event.name if event else payment.event_id,
                "team_name": team.name if team else payment.team_id,
                "package": f"{payment.package_label} x{payment.qty}",
                "amount": payment.amount,
                "votes": payment.votes,
                "status": payment.status.value,
                "supporter_name": payment.supporter_name,
                "supporter_email": payment.supporter_email,
                "supporter_phone": payment.supporter_phone,
                "is_anonymous": payment.is_anonymous,
                "payment_gateway": payment.payment_gateway.value,
                "inquiry_state": duitku_payload.get("_inquiry_state"),
                "needs_manual_review": bool(duitku_payload.get("_needs_manual_review")),
                "created_at": payment.created_at.isoformat(),
                "paid_at": payment.paid_at.isoformat() if payment.paid_at else None,
                "expires_at": payment.expires_at.isoformat() if payment.expires_at else None,
            }
        )
    return ok(result)


@router.post("/payments/{payment_id}/resolve")
async def resolve_payment(
    payment_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    """Tutup transaksi unknown secara manual, dengan alasan dan audit.

    `cancel_unpaid` hanya boleh tanpa referencia, atau setelah provider
    memastikan order tidak bisa dibayar (ABSENT_CONFIRMED). `confirm_paid`
    memakai `process_successful_payment` yang sama dengan jalur otomatis.
    """
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Body JSON tidak valid") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body JSON tidak valid")
    action = body.get("action")
    reason = (body.get("reason") or "").strip()
    if action not in ("cancel_unpaid", "confirm_paid"):
        raise HTTPException(status_code=400, detail="Action harus cancel_unpaid atau confirm_paid")
    if len(reason) < 5:
        raise HTTPException(status_code=400, detail="Alasan minimal 5 karakter")

    payment = await payment_repo.get_payment(session, payment_id, for_update=True)
    if not payment:
        raise HTTPException(status_code=404, detail="Invoice tidak ditemukan")
    payload = dict(payment.duitku_payload or {})

    if action == "cancel_unpaid":
        # Idempoten: invoice yang sudah ditutup dengan alasan yang sama = no-op.
        if payment.status == PaymentStatus.CANCELED and payload.get("_manual_resolution"):
            return ok(
                {
                    "id": payment.id,
                    "status": payment.status.value,
                    "resolution": payload.get("_manual_resolution"),
                },
                "Invoice sudah ditutup sebelumnya",
            )
        absent_verdict = payload.get("_last_verdict") == payment_service.ReconcileVerdict.ABSENT_CONFIRMED.value
        if payment.status != PaymentStatus.PENDING or (payment.duitku_reference and not absent_verdict):
            raise HTTPException(
                status_code=400,
                detail="Invoice hanya bisa dibatalkan bila belum ada referensi atau provider menyatakan tidak bisa dibayar",
            )
        resolution = {"action": action, "reason": reason,
                      "at": datetime.now(timezone.utc).isoformat()}
        payment.status = PaymentStatus.CANCELED
        payment.idempotency_key = None
        payload["_needs_manual_review"] = False
        payload["_inquiry_state"] = "rejected"
        payload["_manual_resolution"] = resolution
        payment.duitku_payload = payload
        await session.flush()
    else:  # confirm_paid
        if payment.status == PaymentStatus.SUCCESS:
            resolution = payload.get("_manual_resolution") or {}
        else:
            # Midtrans legacy tidak punya referensi Duitku; keputusan manual
            # berdasarkan bukti transfer tetap boleh dicatat admin.
            if not payment.duitku_reference and payment.payment_gateway == PaymentGateway.DUITKU:
                raise HTTPException(
                    status_code=400,
                    detail="Invoice belum punya referensi pembayaran; periksa status ke provider terlebih dahulu",
                )
            if payment.status not in (PaymentStatus.PENDING, PaymentStatus.EXPIRED, PaymentStatus.CANCELED):
                raise HTTPException(
                    status_code=400,
                    detail=f"Invoice berstatus {payment.status.value} tidak bisa dikonfirmasi dibayar",
                )
            now = datetime.now(timezone.utc)
            resolution = {"action": action, "reason": reason, "at": now.isoformat()}
            payment.status = PaymentStatus.SUCCESS
            payment.paid_at = payment.paid_at or now
            payment.settled_at = payment.settled_at or now
            payload["_needs_manual_review"] = False
            payload["_manual_resolution"] = resolution
            payment.duitku_payload = payload
            await session.flush()
            # Satu-satunya jalur pemberian vote (idempoten di dalam service).
            await process_successful_payment(session, payment)
            await session.flush()

    await audit_service.record(
        session,
        action="payment.manual_resolve",
        request=request,
        entity_type="payment",
        entity_id=payment.id,
        detail={"action": action, "reason": reason, "status": payment.status.value},
    )
    await session.commit()
    return ok({"id": payment.id, "status": payment.status.value, "resolution": resolution})


@router.get("/audit-logs")
async def list_audit_logs(
    action: str | None = Query(default=None),
    entity_id: str | None = Query(default=None),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db),
):
    logs = await audit_repo.list_audit_logs(
        session,
        limit=limit,
        offset=offset,
        action=action,
        entity_id=entity_id,
        start_date=start_date,
        end_date=end_date,
    )
    return ok(
        [
            {
                "id": log.id,
                "action": log.action,
                "actor_type": log.actor_type.value if log.actor_type else None,
                "actor_name": log.actor_name,
                "entity_type": log.entity_type,
                "entity_id": log.entity_id,
                "detail": log.detail,
                "ip_address": log.ip_address,
                "user_agent": log.user_agent,
                "created_at": log.created_at.isoformat(),
            }
            for log in logs
        ]
    )


@router.get("/settings")
async def get_settings(session: AsyncSession = Depends(get_db)):
    from app.repositories import system_settings as system_settings_repo
    settings = await system_settings_repo.get_bulk(session, [
        "maintenance_mode",
        "leaderboard_refresh",
        "queue_limit",
        "cloudflare_enabled",
        "site_name",
        "admin_email",
        "timezone",
        "currency",
    ])
    return ok(settings)


@router.put("/settings")
async def update_settings(request: Request, session: AsyncSession = Depends(get_db)):
    body = await request.json()
    from app.repositories import system_settings as system_settings_repo
    allowed_keys = ["site_name", "admin_email", "timezone", "currency"]
    for key in allowed_keys:
        if key in body:
            await system_settings_repo.set(session, key, str(body[key]))
    await session.commit()
    return ok(message="Pengaturan berhasil disimpan")


@router.put("/settings/system")
async def update_system_settings(request: Request, session: AsyncSession = Depends(get_db)):
    body = await request.json()
    from app.repositories import system_settings as system_settings_repo
    allowed_keys = ["maintenance_mode", "cloudflare_enabled", "queue_limit", "leaderboard_refresh"]
    for key in allowed_keys:
        if key in body:
            await system_settings_repo.set(session, key, str(body[key]))
    await session.commit()
    return ok(message="Konfigurasi berhasil disimpan")


@router.get("/exports/results")
async def export_results(
    event_id: str = Query(...),
    session: AsyncSession = Depends(get_db),
):
    try:
        content = await export_service.export_vote_results(session, event_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    filename = f"hasil-voting-{event_id}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/exports/payments")
async def export_payments(
    event_id: str = Query(...),
    status: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
):
    content = await export_service.export_payment_report(session, event_id, status=status)
    filename = f"transaksi-{event_id}.xlsx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
