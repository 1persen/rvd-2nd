"""Katalog gift animasi sebagai paket bundling.

Gift adalah paket beli cepat: memilih gift berarti membeli sebanyak `votes`
vote, dan efek animasi adalah bonus dari paket itu, bukan biaya tambahan.

Pembeli tetap bebas membeli jumlah vote berapa pun tanpa gift. Backend hanya
memvalidasi bahwa jika sebuah gift dipilih, qty harus sama dengan paket gift.

Angka vote di sini adalah usulan awal; sesuaikan dengan harga per vote.
"""

GIFTS: dict[str, dict] = {
    "bintang": {"label": "Bintang", "level": "Basic", "votes": 10, "duration_ms": 1500, "animation": "star"},
    "mawar": {"label": "Mawar", "level": "Basic", "votes": 50, "duration_ms": 2000, "animation": "rose"},
    "mahkota": {"label": "Mahkota", "level": "Premium", "votes": 100, "duration_ms": 2500, "animation": "crown"},
    "paus": {"label": "Paus", "level": "Special", "votes": 500, "duration_ms": 4500, "animation": "whale"},
    "roket": {"label": "Roket", "level": "Epic", "votes": 1000, "duration_ms": 3000, "animation": "rocket"},
    "phoenix": {"label": "Phoenix", "level": "Legendary", "votes": 2500, "duration_ms": 4000, "animation": "phoenix"},
    "naga": {"label": "Naga", "level": "Ultimate", "votes": 5000, "duration_ms": 5500, "animation": "dragon"},
}

# Kompatibilitas nama lama yang masih tersimpan di payment.effect_code.
EFFECTS = GIFTS


def gift_votes(code: str) -> int | None:
    """Jumlah vote paket bundling untuk kode gift, atau None jika tidak dikenal."""
    gift = GIFTS.get(code)
    return gift["votes"] if gift else None


def public_effects() -> list[dict]:
    """Satu-satunya sumber katalog gift untuk modal frontend."""
    return [
        {
            "code": code,
            "label": gift["label"],
            "level": gift["level"],
            "votes": gift["votes"],
            "duration_ms": gift["duration_ms"],
            "animation": gift["animation"],
        }
        for code, gift in GIFTS.items()
    ]
