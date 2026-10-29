"""Katalog gift animasi. Syarat dalam poin (vote), bukan biaya tambahan.

Angka vote di sini adalah usulan awal. Sesuaikan dengan harga per vote dan sistem
paket yang berlaku. Backend adalah satu-satunya sumber untuk validasi dan untuk
data yang dikirim ke frontend (public_effects).

Pembeli boleh membeli jumlah vote berapa pun; gift hanya aktif bila qty >= min_votes.
"""

GIFTS: dict[str, dict] = {
    "bintang": {"label": "Bintang", "level": "Basic", "min_votes": 10, "duration_ms": 1500, "animation": "star"},
    "mawar": {"label": "Mawar", "level": "Basic", "min_votes": 50, "duration_ms": 2000, "animation": "rose"},
    "mahkota": {"label": "Mahkota", "level": "Premium", "min_votes": 100, "duration_ms": 2500, "animation": "crown"},
    "paus": {"label": "Paus", "level": "Special", "min_votes": 500, "duration_ms": 4500, "animation": "whale"},
    "roket": {"label": "Roket", "level": "Epic", "min_votes": 1000, "duration_ms": 3000, "animation": "rocket"},
    "phoenix": {"label": "Phoenix", "level": "Legendary", "min_votes": 2500, "duration_ms": 4000, "animation": "phoenix"},
    "naga": {"label": "Naga", "level": "Ultimate", "min_votes": 5000, "duration_ms": 5500, "animation": "dragon"},
}

# Kompatibilitas nama lama yang masih tersimpan di payment.effect_code.
EFFECTS = GIFTS


def effect_min_points(code: str) -> int | None:
    gift = GIFTS.get(code)
    return gift["min_votes"] if gift else None


def public_effects() -> list[dict]:
    """Satu-satunya sumber katalog gift untuk modal frontend."""
    return [
        {
            "code": code,
            "label": gift["label"],
            "level": gift["level"],
            "min_points": gift["min_votes"],
            "duration_ms": gift["duration_ms"],
            "animation": gift["animation"],
        }
        for code, gift in GIFTS.items()
    ]
