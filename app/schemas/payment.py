"""Payment schemas."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.enums.payment_channel import PaymentChannel
from app.enums.payment_gateway import PaymentGateway
from app.enums.payment_status import PaymentStatus


class PaymentSimulateRequest(BaseModel):
    action: str = Field(min_length=1)


class PaymentCreate(BaseModel):
    event_id: str = Field(min_length=1, max_length=40)
    team_id: str = Field(min_length=1, max_length=40)
    qty: int = Field(ge=1)
    supporter_name: str = Field(min_length=1, max_length=100)
    supporter_email: Optional[str] = Field(default=None, max_length=255)
    supporter_phone: str = Field(min_length=1, max_length=20)
    is_anonymous: bool = False
    idempotency_key: Optional[str] = Field(default=None, max_length=64)
    # Kode metode Duitku yang dipilih pelanggan. Kosong = pakai DUITKU_PAYMENT_METHOD.
    payment_method: Optional[str] = Field(default=None, min_length=2, max_length=2, pattern=r"^[A-Z0-9]{2}$")


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    invoice_id: str
    team_id: str
    team_name: str
    event_id: str
    package_code: str
    package_label: str
    qty: int
    amount: int
    votes: int
    status: PaymentStatus
    payment_gateway: PaymentGateway
    payment_channel: Optional[PaymentChannel] = None
    currency: str
    signature_key: Optional[str] = None
    payment_url: Optional[str] = None
    qris_url: Optional[str] = None
    qr_string: Optional[str] = None
    expires_at: Optional[datetime] = None
    webhook_received_at: Optional[datetime] = None
    settled_at: Optional[datetime] = None
    created_at: datetime
    message: str = ""
    supporter_name: str
    supporter_email: Optional[str] = None
    supporter_phone: str
    is_anonymous: bool = False
    is_mock: bool = False
    is_sandbox: bool = False
