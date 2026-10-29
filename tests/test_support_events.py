"""Pesan dukungan tersensor dan syarat bundling efek pada pembuatan invoice."""
import re

import pytest

from app.services import payment_service, profanity_filter
from app.services.payment_service import PaymentError


def test_censor_replaces_only_listed_words(monkeypatch):
    monkeypatch.setattr(profanity_filter, "_pattern", lambda: re.compile(r"\bbodoh\b", re.IGNORECASE))
    assert profanity_filter.censor("kamu bodoh sekali") == "kamu ***** sekali"
    assert profanity_filter.censor("Semangat terus!") == "Semangat terus!"


def test_censor_with_empty_wordlist_is_passthrough(monkeypatch):
    monkeypatch.setattr(profanity_filter, "_pattern", lambda: None)
    assert profanity_filter.censor("apa saja") == "apa saja"


@pytest.mark.asyncio
async def test_created_payment_stores_censored_message_without_effect_cost(test_data, monkeypatch):
    """Efek hanya syarat bundling: pesan tersensor, amount dan votes tidak berubah."""
    from app.core.database import async_session
    from app.schemas.payment import PaymentCreate

    monkeypatch.setattr(profanity_filter, "_pattern", lambda: re.compile(r"\bbodoh\b", re.IGNORECASE))
    payload = PaymentCreate(
        event_id=test_data["event"].id, team_id=test_data["team"].id, qty=25,
        supporter_name="Uji", supporter_email="uji@example.com", supporter_phone="08123456789",
        effect_code="kelinci", support_message="kamu bodoh sekali",
    )
    async with async_session() as session:
        payment = await payment_service.create_payment(session, payload)
        await session.commit()
        assert payment.support_message == "kamu ***** sekali"
        assert payment.effect_code == "kelinci"
        assert payment.amount == 25 * test_data["event"].price_per_vote
        assert payment.votes == 25


@pytest.mark.asyncio
async def test_effect_below_bundling_threshold_is_rejected(test_data):
    from app.core.database import async_session
    from app.schemas.payment import PaymentCreate

    payload = PaymentCreate(
        event_id=test_data["event"].id, team_id=test_data["team"].id, qty=5,
        supporter_name="Uji", supporter_email="uji@example.com", supporter_phone="08123456789",
        effect_code="rocket",
    )
    async with async_session() as session:
        with pytest.raises(PaymentError, match="minimal 10 point"):
            await payment_service.create_payment(session, payload)


@pytest.mark.asyncio
async def test_unknown_effect_is_rejected(test_data):
    from app.core.database import async_session
    from app.schemas.payment import PaymentCreate

    payload = PaymentCreate(
        event_id=test_data["event"].id, team_id=test_data["team"].id, qty=100,
        supporter_name="Uji", supporter_email="uji@example.com", supporter_phone="08123456789",
        effect_code="tidak-ada",
    )
    async with async_session() as session:
        with pytest.raises(PaymentError, match="tidak dikenal"):
            await payment_service.create_payment(session, payload)
