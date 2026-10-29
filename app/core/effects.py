"""Efek animasi dukungan. Syarat bundling dalam poin (vote), bukan biaya tambahan.

Ubah nilai di sini untuk mengatur syarat. Backend memakai min_points untuk
menolak efek jika jumlah poin pesanan kurang dari syarat.
"""

EFFECTS: dict[str, dict] = {
    "rocket": {"label": "Rocket", "min_points": 10},
    "kelinci": {"label": "Kelinci", "min_points": 25},
    "singa": {"label": "Singa", "min_points": 50},
    "phoenix": {"label": "Phoenix", "min_points": 100},
    "paus": {"label": "Paus", "min_points": 250},
    "dragon": {"label": "Dragon", "min_points": 500},
    "kencana": {"label": "Kencana", "min_points": 600},
    "pesiar": {"label": "Pesiar", "min_points": 800},
    "jet": {"label": "Jet Pribadi", "min_points": 1000},
}


def effect_min_points(code: str) -> int | None:
    effect = EFFECTS.get(code)
    return effect["min_points"] if effect else None


# Urutan tampil di modal: efek reguler lalu tier luxury.
LUXURY_CODES = ("kencana", "pesiar", "jet")


def public_effects() -> list[dict]:
    """Satu-satunya sumber syarat bundling untuk modal frontend."""
    return [
        {"code": code, "label": effect["label"], "min_points": effect["min_points"]}
        for code, effect in EFFECTS.items()
    ]
