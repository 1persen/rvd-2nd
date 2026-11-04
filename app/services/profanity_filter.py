"""Sensor kata kasar untuk pesan dukungan.

Daftar kata ada di app/core/profanity_words.txt. Kata yang cocok diganti dengan
'*' sepanjang kata; bagian lain pesan tidak diubah.
"""
import logging
import re
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_WORDS_FILE = Path(__file__).resolve().parents[1] / "core" / "profanity_words.txt"


@lru_cache(maxsize=1)
def _pattern() -> re.Pattern | None:
    if not _WORDS_FILE.exists():
        logger.warning("Daftar kata kasar tidak ditemukan (%s); pesan dukungan tidak disensor.", _WORDS_FILE)
        return None
    words = []
    for line in _WORDS_FILE.read_text(encoding="utf-8").splitlines():
        word = line.strip().lower()
        if word and not word.startswith("#"):
            words.append(re.escape(word))
    if not words:
        logger.warning("Daftar kata kasar kosong; pesan dukungan tidak disensor.")
        return None
    # Longest first so multi-word entries match before their parts.
    words.sort(key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(words) + r")\b", re.IGNORECASE)


def censor(text: str | None) -> str | None:
    if not text:
        return text
    pattern = _pattern()
    if pattern is None:
        return text
    return pattern.sub(lambda m: "*" * len(m.group(0)), text)
