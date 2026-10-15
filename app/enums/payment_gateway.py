from enum import Enum


class PaymentGateway(str, Enum):
    MIDTRANS = "MIDTRANS"
    DUITKU = "DUITKU"
    MANUAL = "MANUAL"
    TRANSFER = "TRANSFER"
