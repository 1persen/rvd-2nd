"""
Duitku API integration for sandbox and production.
"""
import hashlib
import hmac
import logging
import re
import time
from urllib.parse import urlsplit
from typing import Any, Dict, Optional

import httpx

from app.core.config import settings

SANDBOX_BASE = "https://sandbox.duitku.com/webapi/api/merchant"
PRODUCTION_BASE = "https://passport.duitku.com/webapi/api/merchant"
# Total di bawah maxDuration 10s di vercel.json agar RVD sempat menyimpan
# state "unknown" sebelum platform memutus request.
HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=8.0, write=5.0, pool=3.0)

# Duitku transaction status codes (per API specification).
# "00" = successful payment, "01" = pending/processing, "02" = canceled.
DUITKU_STATUS_SUCCESS = "00"
DUITKU_STATUS_PENDING = "01"
DUITKU_STATUS_CANCELED = "02"

# Internal payment status mapping from Duitku status codes.
DUITKU_STATUS_TO_INTERNAL = {
    DUITKU_STATUS_SUCCESS: "SUCCESS",
    DUITKU_STATUS_PENDING: "PENDING",
    DUITKU_STATUS_CANCELED: "CANCELED",
}

logger = logging.getLogger(__name__)


class DuitkuRejected(Exception):
    """Provider menjawab dan secara definitif menolak membuat order."""
    def __init__(self, reason: str = "", status_code: str | None = None) -> None:
        super().__init__(reason or "Duitku rejected the inquiry")
        self.reason = reason
        self.status_code = status_code


class DuitkuUncertain(Exception):
    """Hasil tidak pasti: transport, 5xx, body tak terbaca, atau sukses cacat."""
    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind  # timeout | transport | http_5xx | malformed


def _get_base_url() -> str:
    if settings.DUITKU_ENVIRONMENT == "production":
        return PRODUCTION_BASE
    return SANDBOX_BASE


def generate_signature(merchant_code: str, merchant_order_id: str, payment_amount: int, api_key: str) -> str:
    signing_string = f"{merchant_code}{merchant_order_id}{payment_amount}"
    return hmac.new(api_key.encode("utf-8"), signing_string.encode("utf-8"), hashlib.sha256).hexdigest()


def generate_transaction_status_signature(
    merchant_code: str,
    merchant_order_id: str,
    api_key: str,
) -> str:
    """Signature khusus transactionStatus: HMAC-SHA256(merchantCode + merchantOrderId, apiKey).

    Tidak menyertakan payment_amount, berbeda dari signature inquiry.
    """
    signing_string = f"{merchant_code}{merchant_order_id}"
    return hmac.new(api_key.encode("utf-8"), signing_string.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_callback_signature(merchant_code: str, amount: str, merchant_order_id: str, received_signature: str, api_key: str) -> bool:
    signing_string = f"{merchant_code}{amount}{merchant_order_id}"
    expected_signature = hmac.new(api_key.encode("utf-8"), signing_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected_signature, received_signature)


def normalize_duitku_status(status_code: str) -> Optional[str]:
    """Map Duitku status code to internal payment status.

    Returns None for unknown/unrecognized status codes so callers can
    handle them conservatively without assuming failure.
    """
    return DUITKU_STATUS_TO_INTERNAL.get(status_code)


async def get_payment_methods(
    *,
    amount: int,
    merchant_code: Optional[str] = None,
    api_key: Optional[str] = None,
) -> list[Dict[str, Any]]:
    """Daftar metode pembayaran aktif untuk merchant (read-only, tidak membuat transaksi).

    Signature: HMAC-SHA256(merchantCode + amount + datetime, apiKey).
    """
    merchant_code = merchant_code or settings.DUITKU_MERCHANT_CODE
    api_key = api_key or settings.DUITKU_API_KEY
    if not merchant_code or not api_key:
        raise ValueError("Duitku merchant code or API key not configured")

    datetime_str = time.strftime("%Y-%m-%d %H:%M:%S")
    signature = hmac.new(
        api_key.encode("utf-8"),
        f"{merchant_code}{amount}{datetime_str}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    payload = {
        "merchantcode": merchant_code,
        "amount": str(amount),
        "datetime": datetime_str,
        "signature": signature,
    }
    url = f"{_get_base_url()}/paymentmethod/getpaymentmethod"
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        result = response.json()
    if not isinstance(result, dict) or result.get("responseCode") != "00":
        raise ValueError("Duitku getpaymentmethod response is not successful")
    methods = result.get("paymentFee")
    if not isinstance(methods, list):
        raise ValueError("Duitku getpaymentmethod response has no paymentFee list")
    return methods


def valid_payment_url(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        url = urlsplit(value)
        return (url.scheme == "https" and url.hostname in
                {"passport.duitku.com", "sandbox.duitku.com"}
                and url.port in (None, 443) and not url.username and not url.password)
    except ValueError:
        return False


def is_sandbox_payment_url(value) -> bool:
    return valid_payment_url(value) and urlsplit(value).hostname == "sandbox.duitku.com"


def valid_callback(payload) -> bool:
    if not settings.DUITKU_MERCHANT_CODE or not settings.DUITKU_API_KEY:
        return False
    if not isinstance(payload, dict):
        return False
    fields = ("merchantCode", "amount", "merchantOrderId", "signature")
    if any(not isinstance(payload.get(key), str) or not payload[key] for key in fields):
        return False
    signature = payload["signature"].strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", signature):
        return False
    if payload["merchantCode"] != settings.DUITKU_MERCHANT_CODE:
        return False
    return verify_callback_signature(
        payload["merchantCode"], payload["amount"], payload["merchantOrderId"],
        signature, settings.DUITKU_API_KEY,
    )


async def create_transaction(
    *,
    merchant_order_id: str,
    payment_amount: int,
    payment_method: str,
    product_details: str,
    email: Optional[str],
    customer_va_name: str,
    callback_url: str,
    return_url: str,
    phone_number: Optional[str] = None,
    additional_param: Optional[Dict[str, Any]] = None,
    merchant_user_info: Optional[Dict[str, Any]] = None,
    item_details: Optional[Dict[str, Any]] = None,
    customer_detail: Optional[Dict[str, Any]] = None,
    expiry_period: Optional[int] = None,
    merchant_code: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    merchant_code = merchant_code or settings.DUITKU_MERCHANT_CODE
    api_key = api_key or settings.DUITKU_API_KEY

    if not merchant_code or not api_key:
        raise ValueError("Duitku merchant code or API key not configured")

    signature = generate_signature(
        merchant_code=merchant_code,
        merchant_order_id=merchant_order_id,
        payment_amount=payment_amount,
        api_key=api_key,
    )

    payload: Dict[str, Any] = {
        "merchantCode": merchant_code,
        "paymentAmount": payment_amount,
        "paymentMethod": payment_method,
        "merchantOrderId": merchant_order_id,
        "productDetails": product_details,
        "email": email or "",
        "customerVaName": customer_va_name,
        "callbackUrl": callback_url,
        "returnUrl": return_url,
        "signature": signature,
    }
    if phone_number:
        payload["phoneNumber"] = phone_number
    if additional_param:
        payload["additionalParam"] = additional_param
    if merchant_user_info:
        payload["merchantUserInfo"] = merchant_user_info
    if item_details:
        payload["itemDetails"] = item_details
    if customer_detail:
        payload["customerDetail"] = customer_detail
    if expiry_period:
        payload["expiryPeriod"] = expiry_period

    url = f"{_get_base_url()}/v2/inquiry"
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.post(url, json=payload)
    except httpx.TimeoutException as exc:
        elapsed = time.monotonic() - start
        logger.warning(
            "Duitku inquiry timeout for order %s (method=%s, elapsed=%.2fs)",
            merchant_order_id, payment_method, elapsed,
        )
        raise DuitkuUncertain("timeout", type(exc).__name__) from exc
    except httpx.HTTPError as exc:
        elapsed = time.monotonic() - start
        logger.warning(
            "Duitku inquiry transport error for order %s (method=%s, elapsed=%.2fs): %s",
            merchant_order_id, payment_method, elapsed, type(exc).__name__,
        )
        raise DuitkuUncertain("transport", type(exc).__name__) from exc
    except Exception as exc:
        elapsed = time.monotonic() - start
        logger.warning(
            "Duitku inquiry unexpected error for order %s (method=%s, elapsed=%.2fs): %s",
            merchant_order_id, payment_method, elapsed, type(exc).__name__,
        )
        raise

    # Body dibaca sebelum status diputuskan: 4xx bisa membawa penolakan yang
    # definitif, sedangkan body tak terbaca tetap "tidak pasti".
    status = response.status_code
    try:
        result = response.json()
    except (ValueError, TypeError):
        result = None

    if status >= 500:
        elapsed = time.monotonic() - start
        logger.warning(
            "Duitku inquiry http_5xx for order %s (method=%s, status=%s, elapsed=%.2fs)",
            merchant_order_id, payment_method, status, elapsed,
        )
        raise DuitkuUncertain("http_5xx", f"status={status}")
    if status == 429:
        elapsed = time.monotonic() - start
        logger.warning(
            "Duitku inquiry rate limited for order %s (method=%s, elapsed=%.2fs)",
            merchant_order_id, payment_method, elapsed,
        )
        raise DuitkuUncertain("transport", "rate_limited")
    if status >= 400:
        elapsed = time.monotonic() - start
        logger.warning(
            "Duitku inquiry HTTP error for order %s (method=%s, status=%s, elapsed=%.2fs)",
            merchant_order_id, payment_method, status, elapsed,
        )
        if isinstance(result, dict) and (result.get("statusCode") or result.get("Message")):
            raise DuitkuRejected(
                str(result.get("statusMessage") or result.get("Message") or "").strip(),
                str(result.get("statusCode") or "") or None,
            )
        raise DuitkuUncertain("transport", f"status={status}")
    if not isinstance(result, dict):
        elapsed = time.monotonic() - start
        logger.warning(
            "Duitku inquiry malformed for order %s (method=%s, elapsed=%.2fs): non_object",
            merchant_order_id, payment_method, elapsed,
        )
        raise DuitkuUncertain("malformed", "non_object")

    elapsed = time.monotonic() - start
    status_code = result.get("statusCode")
    reference = result.get("reference")
    logger.info(
        "Duitku inquiry for order %s (statusCode=%s, reference=%s, elapsed=%.2fs)",
        merchant_order_id, status_code, reference, elapsed,
    )
    if status_code == "00":
        if not reference or not valid_payment_url(result.get("paymentUrl")):
            # Catat apa yang dikirim Duitku agar penyebab kegagalan diketahui
            # (metode tidak aktif, amount tidak sesuai, kredensial, dll).
            # Tidak ada data rahasia: bukan signature dan bukan API key.
            logger.warning(
                "Duitku inquiry malformed for order %s (method=%s, environment=%s): %s",
                merchant_order_id, payment_method, settings.DUITKU_ENVIRONMENT,
                {
                    **result,
                    "paymentUrl": "valid" if valid_payment_url(result.get("paymentUrl")) else repr(result.get("paymentUrl")),
                },
            )
            raise DuitkuUncertain("malformed", "success_without_url")
        return result
    if status_code:
        logger.warning(
            "Duitku inquiry rejected for order %s (method=%s, environment=%s): %s",
            merchant_order_id, payment_method, settings.DUITKU_ENVIRONMENT,
            {
                **result,
                "paymentUrl": "valid" if valid_payment_url(result.get("paymentUrl")) else repr(result.get("paymentUrl")),
            },
        )
        raise DuitkuRejected(
            str(result.get("statusMessage") or result.get("Message") or "").strip(),
            str(status_code),
        )
    raise DuitkuUncertain("malformed", "no_status_code")


async def check_transaction_status(
    *,
    merchant_order_id: str,
    merchant_code: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Verifikasi status transaksi melalui endpoint transactionStatus Duitku.

    Digunakan untuk rekonsiliasi saat inquiry timeout atau gagal. Tidak dianggap
    bukti pembayaran sukses hanya karena HTTP 200; status bisnis dalam respons
    harus dipetakan oleh pemanggil.

    Tidak mencatat API key, signature lengkap, atau informasi sensitif ke log.
    """
    merchant_code = merchant_code or settings.DUITKU_MERCHANT_CODE
    api_key = api_key or settings.DUITKU_API_KEY

    if not merchant_code or not api_key:
        raise ValueError("Duitku merchant code or API key not configured")

    signature = generate_transaction_status_signature(
        merchant_code=merchant_code,
        merchant_order_id=merchant_order_id,
        api_key=api_key,
    )

    payload: Dict[str, Any] = {
        "merchantCode": merchant_code,
        "merchantOrderId": merchant_order_id,
        "signature": signature,
    }

    url = f"{_get_base_url()}/transactionStatus"
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            result = response.json()
    except httpx.TimeoutException as exc:
        elapsed = time.monotonic() - start
        logger.warning(
            "Duitku transactionStatus timeout for order %s (elapsed=%.2fs)",
            merchant_order_id, elapsed,
        )
        raise
    except httpx.HTTPStatusError as exc:
        elapsed = time.monotonic() - start
        logger.warning(
            "Duitku transactionStatus HTTP error for order %s (status=%s, elapsed=%.2fs)",
            merchant_order_id, exc.response.status_code, elapsed,
        )
        raise
    except (ValueError, TypeError) as exc:
        elapsed = time.monotonic() - start
        logger.warning(
            "Duitku transactionStatus invalid JSON for order %s (elapsed=%.2fs): %s",
            merchant_order_id, elapsed, type(exc).__name__,
        )
        raise
    except Exception as exc:
        elapsed = time.monotonic() - start
        logger.warning(
            "Duitku transactionStatus unexpected error for order %s (elapsed=%.2fs): %s",
            merchant_order_id, elapsed, type(exc).__name__,
        )
        raise

    elapsed = time.monotonic() - start
    status_code = result.get("statusCode") if isinstance(result, dict) else None
    reference = result.get("reference") if isinstance(result, dict) else None
    logger.info(
        "Duitku transactionStatus for order %s (statusCode=%s, reference=%s, elapsed=%.2fs)",
        merchant_order_id, status_code, reference, elapsed,
    )

    if not isinstance(result, dict):
        raise ValueError("Duitku transactionStatus response is not a JSON object")

    return result
