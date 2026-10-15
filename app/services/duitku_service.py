"""
Duitku API integration for sandbox and production.
"""
import hashlib
import hmac
import re
from urllib.parse import urlsplit
from typing import Any, Dict, Optional

import httpx

from app.core.config import settings

SANDBOX_BASE = "https://sandbox.duitku.com/webapi/api/merchant"
PRODUCTION_BASE = "https://passport.duitku.com/webapi/api/merchant"
HTTP_TIMEOUT = 15.0


def _get_base_url() -> str:
    if settings.DUITKU_ENVIRONMENT == "production":
        return PRODUCTION_BASE
    return SANDBOX_BASE


def generate_signature(merchant_code: str, merchant_order_id: str, payment_amount: int, api_key: str) -> str:
    signing_string = f"{merchant_code}{merchant_order_id}{payment_amount}"
    return hmac.new(api_key.encode("utf-8"), signing_string.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_callback_signature(merchant_code: str, amount: str, merchant_order_id: str, received_signature: str, api_key: str) -> bool:
    signing_string = f"{merchant_code}{amount}{merchant_order_id}"
    expected_signature = hmac.new(api_key.encode("utf-8"), signing_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected_signature, received_signature)


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
    if not re.fullmatch(r"[0-9a-f]{64}", payload["signature"]):
        return False
    if payload["merchantCode"] != settings.DUITKU_MERCHANT_CODE:
        return False
    return verify_callback_signature(
        payload["merchantCode"], payload["amount"], payload["merchantOrderId"],
        payload["signature"], settings.DUITKU_API_KEY,
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
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        return response.json()
