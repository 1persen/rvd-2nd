"""Isi data dummy running text dukungan untuk satu event.

Hanya menyentuh baris dengan prefix 'seed-' (aman dihapus ulang).
Menolak database non-lokal kecuali --allow-remote diberikan.

Contoh:
    python scripts/seed_support_feed.py --event evt_xxx --count 12
    python scripts/seed_support_feed.py --event evt_xxx --clear
"""
import argparse
import asyncio
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import async_session  # noqa: E402
from app.enums.payment_gateway import PaymentGateway  # noqa: E402
from app.enums.payment_status import PaymentStatus  # noqa: E402
from app.models.payment import Payment  # noqa: E402
from app.models.team import Team  # noqa: E402

PREFIX = "seed-"
NAMES = ["Rina", "Budi", "Sari", "Dewi", "Agus", "Yusuf", "Nadia", "Fajar", "Lestari", "Hendra"]
MESSAGES = [
    "Semangat untuk jagoanmu!",
    "Sukses selalu, kamu hebat!",
    "Terus berjuang, kamu bisa!",
    "Bangga dengan perjuanganmu.",
    "",
    "Ayo terus maju!",
]
EFFECTS = [None, None, None, "bintang", "mawar", "paus"]
VOTES = [1, 5, 10, 25, 50, 500]


def _is_local(url: str) -> bool:
    return any(host in url for host in ("localhost", "127.0.0.1", "@db:", "@postgres:"))


async def run(event_id: str, count: int, clear: bool, allow_remote: bool) -> None:
    if not allow_remote and not _is_local(settings.DATABASE_URL):
        raise SystemExit("Database bukan lokal. Jalankan dengan --allow-remote jika memang disengaja.")

    async with async_session() as session:
        await session.execute(
            delete(Payment).where(Payment.event_id == event_id, Payment.id.like(PREFIX + "%"))
        )
        if clear:
            await session.commit()
            print("Data seed dihapus.")
            return

        team = (await session.execute(
            select(Team).where(Team.event_id == event_id, Team.deleted_at.is_(None)).limit(1)
        )).scalar_one_or_none()
        if team is None:
            raise SystemExit("Event ini belum punya tim.")

        now = datetime.now(timezone.utc)
        for i in range(count):
            votes = random.choice(VOTES)
            effect = random.choice(EFFECTS)
            name = random.choice(NAMES)
            session.add(Payment(
                id=f"{PREFIX}{event_id[-6:]}-{i:03d}",
                team_id=team.id,
                event_id=event_id,
                supporter_name=name,
                supporter_email=None,
                supporter_phone="+620000000000",
                is_anonymous=random.random() < 0.15,
                package_code="seed",
                package_label="Data dummy",
                qty=votes,
                amount=votes * 10000,
                votes=votes,
                vote_snapshot={"seed": True},
                status=PaymentStatus.SUCCESS,
                payment_gateway=PaymentGateway.DUITKU,
                currency="IDR",
                support_message=random.choice(MESSAGES) or None,
                effect_code=effect,
                paid_at=now - timedelta(minutes=i),
                created_at=now - timedelta(minutes=i),
                updated_at=now - timedelta(minutes=i),
            ))
        await session.commit()
        print(f"{count} dukungan dummy ditambahkan untuk {event_id}.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True)
    parser.add_argument("--count", type=int, default=12)
    parser.add_argument("--clear", action="store_true")
    parser.add_argument("--allow-remote", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.event, args.count, args.clear, args.allow_remote))


if __name__ == "__main__":
    main()
