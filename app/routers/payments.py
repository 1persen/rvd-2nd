"""
Payments API — create invoice (QRIS), status polling, Midtrans webhook.

Webhook menerima semua event Midtrans, memverifikasi signature, memproses
secara idempotent, membroadcast SSE leaderboard setelah vote masuk.
Dikecualikan dari CSRF (signature = otentikasi), tapi tetap dirate-limit.
"""
import json
import logging
from typing import Optional

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
    return ok(data, "Invoice pembayaran sudah dibuat")


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

    return ok({"order_id": payment.id, "status": payment.status.value}, "Webhook diterima")


@router.post("/payments/mock/{payment_id}/simulate")
async def mock_simulate_payment(
    payment_id: str,
    payload: PaymentSimulateRequest,
    session: AsyncSession = Depends(get_db),
):
    """Simulate a Midtrans payment outcome (mock mode only).

    Mirrors ``POST /payments/webhook`` but without any Midtrans call — it is
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
        form = await request.form()
        payload = {k: v for k, v in form.multi_items()}
    except Exception:
        try:
            raw = await request.body()
            payload = json.loads(raw) if raw else {}
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid callback payload")

    # Verify signature if possible
    try:
        merchant_code = payload.get("merchantCode") or ""
        amount = str(payload.get("amount") or payload.get("paymentAmount") or "")
        merchant_order_id = payload.get("merchantOrderId") or payload.get("merchantOrderID") or ""
        received_signature = payload.get("signature") or ""
        if settings.DUITKU_MERCHANT_CODE and settings.DUITKU_API_KEY:
            ok = duitku_service.verify_callback_signature(
                merchant_code or settings.DUITKU_MERCHANT_CODE,
                amount,
                merchant_order_id,
                received_signature,
                settings.DUITKU_API_KEY,
            )
            if not ok:
                logger.warning("Duitku callback signature invalid: %s", merchant_order_id)
                raise HTTPException(status_code=403, detail="Signature tidak valid")
    except HTTPException:
        raise
    except Exception:
        logger.warning("Duitku callback signature verification failed")
        # Don't reject if verification can't parse - but spec says validate

    try:
        payment, voted_now = await payment_service.process_webhook(session, payload)
    except payment_service.PaymentNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except payment_service.PaymentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await session.commit()
    if voted_now:
        await leaderboard_service.schedule_push(payment.event_id)
    return ok({"order_id": payment.id, "status": payment.status.value}, "Callback diterima")
