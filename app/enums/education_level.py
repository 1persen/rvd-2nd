"""Centralized education level / event category definitions.

Single source of truth for the four category values used by both events
(`event.category`) and teams (`team.education_level`).
"""
from typing import Tuple

EDUCATION_LEVELS: Tuple[str, ...] = (
    "sma-smk-ma",
    "smp-mts",
    "sd-mi",
    "purna-klub",
)

EDUCATION_LABELS: dict[str, str] = {
    "sma-smk-ma": "SMA/SMK/MA",
    "smp-mts": "SMP/MTs",
    "sd-mi": "SD/MI",
    "purna-klub": "PURNA/KLUB",
}

ALLOWED_EDUCATION_LEVELS: set[str] = set(EDUCATION_LEVELS)
ALLOWED_EVENT_CATEGORIES: set[str] = set(EDUCATION_LEVELS)

# Ordering used when sorting available levels.
LEVEL_ORDER: dict[str, int] = {value: index for index, value in enumerate(EDUCATION_LEVELS)}
