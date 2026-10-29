"""
Payments API â€” create invoice (QRIS), status polling, Midtrans webhook.

Webhook menerima semua event Midtrans, memverifikasi signature, memproses
secara idempotent, membroadcast SSE leaderboard setelah vote masuk.
Dikecualikan dari CSRF (signature = otentikasi), tapi tetap dirate-limit.
"""
import json
import logging
from typing import Optional
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.enums.payment_status import PaymentStatus
from app.repositories import payment as payment_repo
from app.repositories import team as team_repo
from app.schemas.common import ok
from app.schemas.payment import PaymentCreate, PaymentSimulateRequest
from app.services import leaderboard_service, payment_service, duitku_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["payments"])


@router.post("/payments")
async def create_payment(
    payload: PaymentCreate,
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    idempotency_key = request.headers.get("Idempotency-Key")
    if idempotency_key:
        # The header bypasses the schema's max_length; the column is varchar(64).
        if len(idempotency_key) > 64:
            raise HTTPException(status_code=400, detail="Idempotency-Key maksimal 64 karakter")
        payload.idempotency_key = idempotency_key
    try:
        payment = await payment_service.create_payment(session, payload)
    except payment_service.PaymentUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except payment_service.PaymentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    team = await team_repo.get_team(session, payment.team_id)
    data = payment_service.to_payment_out(payment, team).model_dump()
    await session.commit()
    if payment.status == PaymentStatus.SUCCESS:
        await leaderboard_service.schedule_push(payment.event_id)
    return ok(data, "Invoice pembayaran sudah dibuat")


@router.get("/payments/methods")
async def list_payment_methods(amount: int = 10000):
    """Metode pembayaran Duitku yang aktif untuk ditampilkan ke pelanggan."""
    if amount < 1 or amount > 100_000_000:
        raise HTTPException(status_code=400, detail="Nominal tidak valid")
    try:
        methods = await payment_service.get_active_payment_methods(amount)
    except payment_service.PaymentUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("Payment method list unavailable (error=%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Metode pembayaran belum dapat dimuat.") from exc
    data = [
        {"code": m.get("paymentMethod"), "name": m.get("paymentName"),
         "image": m.get("paymentImage"), "fee": m.get("totalFee")}
        for m in methods if m.get("paymentMethod")
    ]
    return ok(data)


@router.get("/payments/{payment_id}/status")
async def payment_status(
    payment_id: str,
    session: AsyncSession = Depends(get_db),
):
    payment = await payment_repo.get_payment(session, payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="Invoice tidak ditemukan")

    # Invoice kedaluwarsa secara lokal jika melewati expires_at.
    if payment.status == PaymentStatus.PENDING and payment.expires_at:
        from datetime import datetime, timezone

        if datetime.now(timezone.utc) > payment.expires_at:
            await payment_repo.expire_if_pending(session, payment.id)
            await session.commit()
            await session.refresh(payment)

    team = await team_repo.get_team(session, payment.team_id)
    data = payment_service.to_payment_out(payment, team).model_dump()
    return ok(data)


@router.post("/payments/{payment_id}/reconcile")
async def reconcile_payment(payment_id: str, session: AsyncSession = Depends(get_db)):
    """Explicit recovery, separately throttled per invoice across workers."""
    try:
        payment, voted_now = await payment_service.reconcile_duitku_payment(session, payment_id)
    except payment_service.PaymentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except payment_service.PaymentUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except payment_service.PaymentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    team = await team_repo.get_team(session, payment.team_id)
    data = payment_service.to_payment_out(payment, team).model_dump()
    await session.commit()
    if voted_now:
        await leaderboard_service.schedule_push(payment.event_id)
        await leaderboard_service.publish_support(session, payment)
    return ok(data)


@router.post("/payments/webhook")
async def midtrans_webhook(request: Request, session: AsyncSession = Depends(get_db)):
    raw = await request.body()
    try:
        payload = json.loads(raw)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Payload JSON tidak valid") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Payload harus berupa JSON object")

    if not payment_service.verify_webhook_signature(payload, raw):
        logger.warning("Webhook signature invalid: %s", payload.get("order_id"))
        raise HTTPException(status_code=403, detail="Signature tidak valid")

    try:
        payment, voted_now = await payment_service.process_webhook(session, payload)
    except payment_service.PaymentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except payment_service.PaymentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await session.commit()

    if voted_now:
        await leaderboard_service.schedule_push(payment.event_id)
        await leaderboard_service.publish_support(session, payment)

    return ok({"order_id": payment.id, "status": payment.status.value}, "Webhook diterima")


@router.post("/payments/mock/{payment_id}/simulate")
async def mock_simulate_payment(
    payment_id: str,
    payload: PaymentSimulateRequest,
    session: AsyncSession = Depends(get_db),
):
    """Simulate a Midtrans payment outcome (mock mode only).

    Mirrors ``POST /payments/webhook`` but without any Midtrans call â€” it is
    gated on explicit mock mode (``MIDTRANS_MOCK_MODE`` with no server key),
    the same condition that produces the mock QRIS in ``create_payment``.
    Otherwise the endpoint is intentionally hidden (404).
    """
    if not settings.midtrans_mock_enabled:
        raise HTTPException(status_code=404, detail="Mock endpoint tidak tersedia")

    try:
        payment = await payment_service.simulate_payment_status(
            session, payment_id, payload.action
        )
    except payment_service.PaymentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    team = await team_repo.get_team(session, payment.team_id)
    data = payment_service.to_payment_out(payment, team).model_dump()
    await session.commit()
    if payment.status == PaymentStatus.SUCCESS:
        await leaderboard_service.schedule_push(payment.event_id)
    return ok(data, "Simulasi status pembayaran")


@router.post("/payments/duitku/callback")
async def duitku_callback(request: Request, session: AsyncSession = Depends(get_db)):
    try:
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        raw = await request.body()
        if len(raw) > 16384:
            raise HTTPException(status_code=413, detail="Callback payload too large")
        if content_type == "application/json":
            payload = json.loads(raw)
        elif content_type in ("application/x-www-form-urlencoded", ""):
            pairs = parse_qsl(raw.decode("utf-8"), keep_blank_values=True,
                              errors="strict", max_num_fields=64)
            payload = dict(pairs)
            if len(payload) != len(pairs):
                raise ValueError("Duplicate callback fields")
        else:
            raise HTTPException(status_code=415, detail="Unsupported callback content type")
    except (ValueError, UnicodeError):
        raise HTTPException(status_code=400, detail="Invalid callback payload")
    if not duitku_service.valid_callback(payload):
        raise HTTPException(status_code=403, detail="Signature tidak valid")
    try:
        payment, voted_now = await payment_service.process_duitku_callback(session, payload)
    except payment_service.PaymentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except payment_service.PaymentUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except payment_service.PaymentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    if voted_now:
        await leaderboard_service.schedule_push(payment.event_id)
        await leaderboard_service.publish_support(session, payment)
    return ok({"order_id": payment.id, "status": payment.status.value}, "Callback diterima")
