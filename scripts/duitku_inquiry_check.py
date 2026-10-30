"""Cek langsung respons API Duitku untuk satu order uji.

Menampilkan respons mentah inquiry supaya penyebab "Tautan pembayaran belum
tersedia" bisa diketahui: metode tidak aktif di merchant, amount tidak sesuai,
signature salah, atau paymentUrl kosong.

Contoh:
    python scripts/duitku_inquiry_check.py --method NQ --amount 1000
    python scripts/duitku_inquiry_check.py --method SA --amount 1100 --env production
"""
import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.services import duitku_service  # noqa: E402


async def run(method: str, amount: int) -> None:
    print(f"environment : {settings.DUITKU_ENVIRONMENT}")
    print(f"merchant    : {settings.DUITKU_MERCHANT_CODE or '(kosong)'}")
    print(f"api key     : {'terisi' if settings.DUITKU_API_KEY else '(kosong)'}")
    print(f"base url    : {duitku_service._get_base_url()}")
    print(f"method      : {method}")
    print(f"amount      : {amount}")
    if not settings.DUITKU_MERCHANT_CODE or not settings.DUITKU_API_KEY:
        print("\nKredensial Duitku kosong: tidak ada invoice yang bisa dibuat.")
        return

    order_id = f"check-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    try:
        result = await duitku_service.create_transaction(
            merchant_order_id=order_id,
            payment_amount=amount,
            payment_method=method,
            product_details="Uji koneksi Duitku",
            email="check@example.com",
            customer_va_name="Uji Duitku",
            callback_url=f"{settings.APP_BASE_URL}/api/v1/payments/duitku/callback",
            return_url=f"{settings.APP_BASE_URL}/",
            expiry_period=10,
        )
    except Exception as exc:  # noqa: BLE001 - tujuannya menampilkan error apa pun
        print(f"\nGAGAL memanggil Duitku: {type(exc).__name__}: {exc}")
        response = getattr(exc, "response", None)
        if response is not None:
            print(f"HTTP {response.status_code}: {response.text[:500]}")
        return

    print("\nRespons Duitku:")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("\nRingkasan:")
    print(f"  statusCode valid  : {result.get('statusCode') == '00'}")
    print(f"  reference ada     : {bool(result.get('reference'))}")
    print(f"  paymentUrl valid  : {duitku_service.valid_payment_url(result.get('paymentUrl'))}")
    print(f"  paymentUrl        : {result.get('paymentUrl')}")
    print(f"  qrString ada      : {bool(result.get('qrString'))}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--method", default="NQ", help="NQ (QRIS), SA (ShopeePay), DA (DANA)")
    parser.add_argument("--amount", type=int, default=1000)
    parser.add_argument("--env", choices=["sandbox", "production"])
    args = parser.parse_args()
    if args.env:
        settings.DUITKU_ENVIRONMENT = args.env
    asyncio.run(run(args.method, args.amount))


if __name__ == "__main__":
    main()
