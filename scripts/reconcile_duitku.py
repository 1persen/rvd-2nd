"""Jalankan satu putaran rekonsiliasi transaksi Duitku yang belum pasti.

Memilih invoice Duitku PENDING yang inquiry-nya `unknown`/`creating`, belum
memasuki manual review, dan sudah jatuh tempo pemeriksaannya (`_next_check_at`),
lalu memanggil `reconcile_duitku_payment` untuk masing-masing. Transaksi unknown
tidak pernah diturunkan menjadi gagal; keputusan akhir tetap lewat provider atau
admin.

Contoh:
    python scripts/reconcile_duitku.py --limit 50              # dry-run (default)
    python scripts/reconcile_duitku.py --limit 50 --apply      # menulis
    python scripts/reconcile_duitku.py --window-hours 6 --apply

Dry-run tidak menulis apa pun, termasuk `_attempts` dan lease. Jalankan dengan
DATABASE_URL test; jangan menunjuk database produksi.
"""
import argparse
import asyncio
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import async_session  # noqa: E402
from app.enums.payment_gateway import PaymentGateway  # noqa: E402
from app.enums.payment_status import PaymentStatus  # noqa: E402
from app.models.payment import Payment  # noqa: E402
from app.services import payment_service  # noqa: E402

CANDIDATE_SCAN_LIMIT = 500
RECONCILE_STATES = ("unknown", "creating")


async def select_candidates(window_hours: int, limit: int) -> list[dict]:
    """Filter SQL hanya pada kolom biasa; state JSON difilter di Python."""
    since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    stmt = (
        select(Payment)
        .where(Payment.payment_gateway == PaymentGateway.DUITKU)
        .where(Payment.status == PaymentStatus.PENDING)
        .where(Payment.created_at >= since)
        .order_by(Payment.created_at.asc())
        .limit(CANDIDATE_SCAN_LIMIT)
    )
    now = time.time()
    async with async_session() as session:
        rows = (await session.execute(stmt)).scalars().all()
        candidates = []
        for payment in rows:
            payload = payment.duitku_payload or {}
            if payload.get("_inquiry_state") not in RECONCILE_STATES:
                continue
            if payload.get("_needs_manual_review"):
                continue
            if payload.get("_next_check_at", 0) > now:
                continue
            candidates.append({"id": payment.id})
            if len(candidates) >= limit:
                break
        return candidates


async def reconcile_one(payment_id: str) -> dict:
    """Satu invoice, satu session: hasil untuk baris log."""
    async with async_session() as session:
        result = await payment_service.reconcile_duitku_payment(session, payment_id)
        await session.commit()
        payload = result.payment.duitku_payload or {}
        transaction = payload.get("_transaction_status") or {}
        return {
            "payment_id": payment_id,
            "verdict": result.verdict.value,
            "attempts": payload.get("_attempts"),
            "status_code": transaction.get("statusCode"),
            "status": result.payment.status.value,
            "voted_now": result.voted_now,
        }


async def run(limit: int, window_hours: int, apply: bool) -> int:
    print(f"database    : {'sqlite' if settings.is_sqlite else 'postgresql'}")
    print(f"environment : {settings.DUITKU_ENVIRONMENT}")
    print(f"mode        : {'APPLY' if apply else 'DRY-RUN (tidak menulis)'}")
    print(f"window      : {window_hours} jam, limit {limit}")
    if not settings.DUITKU_MERCHANT_CODE or not settings.DUITKU_API_KEY:
        print("\nKredensial Duitku kosong: rekonsiliasi tidak bisa memanggil provider.")
        return 1

    candidates = await select_candidates(window_hours, limit)
    print(f"\nkandidat    : {len(candidates)}")
    if not apply:
        for candidate in candidates:
            print(f"  [dry-run] {candidate['id']}")
        print("\nDry-run selesai: tidak ada baris yang diubah.")
        return 0

    counts: dict[str, int] = {}
    failures = 0
    for candidate in candidates:
        payment_id = candidate["id"]
        try:
            outcome = await reconcile_one(payment_id)
        except Exception as exc:  # noqa: BLE001 - log lalu lanjut ke item berikutnya
            failures += 1
            print(f"  {payment_id} ERROR {type(exc).__name__}: {exc}")
            continue
        counts[outcome["verdict"]] = counts.get(outcome["verdict"], 0) + 1
        print(
            f"  {payment_id} verdict={outcome['verdict']} attempts={outcome['attempts']} "
            f"statusCode={outcome['status_code'] or '-'} status={outcome['status']} "
            f"voted_now={outcome['voted_now']}"
        )

    summary = ", ".join(f"{name}={count}" for name, count in sorted(counts.items())) or "tidak ada"
    print(f"\nringkasan   : {summary}; error={failures}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Rekonsiliasi transaksi Duitku yang belum pasti")
    parser.add_argument("--limit", type=int, default=50, help="jumlah invoice maksimum per putaran")
    parser.add_argument("--window-hours", type=int, default=24, help="hanya invoice dalam N jam terakhir")
    parser.add_argument("--apply", action="store_true", help="tulis hasil (default: dry-run)")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit minimal 1")
    if args.window_hours < 1:
        parser.error("--window-hours minimal 1")
    exit_code = asyncio.run(run(args.limit, args.window_hours, args.apply))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
