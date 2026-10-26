"""Per-event branding assets: validation, normalization, and Supabase Storage.

Browser uploads never go straight to Storage. The backend re-encodes raster
images (stripping metadata), sanitizes SVG, validates WOFF2, then uploads a new
object with a unique key. The public bucket is only for non-sensitive branding.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import re
import threading
import uuid
from dataclasses import dataclass
from typing import Optional

import httpx

from app.core.config import settings
from app.services.event_page_service import managed_asset_key

logger = logging.getLogger(__name__)

MAX_TOTAL_BODY = 17 * 1024 * 1024
SLOT_INPUT_LIMIT = {
    "background": 5 * 1024 * 1024,
    "logo": 5 * 1024 * 1024,
    "poster": 5 * 1024 * 1024,
    "ornament": 512 * 1024,
    "font": 1024 * 1024,
}
SLOT_OUTPUT_LIMIT = {
    "background": 700 * 1024,
    "poster": 500 * 1024,
    "logo": 250 * 1024,
}
SLOT_EXTENSION = {"background": "webp", "logo": "webp", "poster": "webp", "ornament": "svg", "font": "woff2"}
SLOT_CONTENT_TYPE = {
    "background": "image/webp",
    "logo": "image/webp",
    "poster": "image/webp",
    "ornament": "image/svg+xml",
    "font": "font/woff2",
}
ASSET_SLOTS = tuple(SLOT_INPUT_LIMIT)

_RASTER_FORMATS = {"JPEG", "PNG", "WEBP"}
_MAX_PIXELS = 24_000_000
# A WOFF2 file is a compressed container: 1 MiB of input can decode into a much
# larger SFNT. Cap the declared decoded size and the number of tables so a small
# upload cannot force a large allocation during validation.
_MAX_SFNT_BYTES = 40 * 1024 * 1024
_MAX_SFNT_TABLES = 4096
# Pillow releases the GIL while encoding, so a burst of uploads would otherwise
# saturate every CPU. The semaphore is a thread primitive because the work runs
# in a worker thread, which keeps it independent of the asyncio event loop.
_RASTER_CONCURRENCY = 2
_raster_semaphore = threading.BoundedSemaphore(_RASTER_CONCURRENCY)


class AssetValidationError(ValueError):
    """The uploaded bytes are not a safe asset for the requested slot."""


class StorageUnavailable(RuntimeError):
    """Supabase Storage is not configured on this deployment."""


class StorageOperationError(RuntimeError):
    """Storage rejected a request or could not be reached."""


@dataclass(frozen=True)
class NormalizedAsset:
    slot: str
    data: bytes
    content_type: str
    extension: str

    def data_url(self) -> str:
        encoded = base64.b64encode(self.data).decode("ascii")
        return f"data:{self.content_type};base64,{encoded}"


@dataclass(frozen=True)
class ManagedAsset:
    url: str
    key: str
    content_type: str
    size: int


# --- Raster -----------------------------------------------------------------
def _fit(width: int, height: int, max_width: int, max_height: int) -> tuple[int, int]:
    scale = min(1.0, max_width / width, max_height / height)
    return max(1, round(width * scale)), max(1, round(height * scale))


def _encode_webp(image, slot: str) -> bytes:
    from PIL import Image

    target = SLOT_OUTPUT_LIMIT[slot]
    width, height = image.size
    for _ in range(4):
        for quality in (82, 76, 70, 62, 54):
            buffer = io.BytesIO()
            image.save(buffer, format="WEBP", quality=quality, method=4)
            data = buffer.getvalue()
            if len(data) <= target:
                return data
        if width <= 320 or height <= 320:
            break
        width, height = max(1, round(width * 0.85)), max(1, round(height * 0.85))
        image = image.resize((width, height), Image.Resampling.LANCZOS)
    raise AssetValidationError(
        f"Gambar {slot} tetap lebih dari {target // 1024} KiB setelah dikompresi"
    )


def _normalize_raster(slot: str, data: bytes) -> NormalizedAsset:
    import warnings

    from PIL import Image, ImageOps, UnidentifiedImageError

    Image.MAX_IMAGE_PIXELS = _MAX_PIXELS
    # Bounded here, inside the worker thread, so concurrent uploads queue for
    # CPU instead of all decoding at once.
    with _raster_semaphore, warnings.catch_warnings():
        # Pillow only warns between MAX_IMAGE_PIXELS and twice that value; treat
        # the warning as fatal so an oversized decode never proceeds.
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with Image.open(io.BytesIO(data)) as source:
                if source.format not in _RASTER_FORMATS:
                    raise AssetValidationError("Format gambar harus JPEG, PNG, atau WebP")
                source.seek(0)
                image = ImageOps.exif_transpose(source)
                if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
                    image = image.convert("RGBA")
                else:
                    image = image.convert("RGB")
                max_width, max_height = {
                    "background": (1920, 1920),
                    "poster": (1600, 2000),
                    "logo": (800, 800),
                }[slot]
                size = _fit(image.width, image.height, max_width, max_height)
                if size != image.size:
                    image = image.resize(size, Image.Resampling.LANCZOS)
                encoded = _encode_webp(image, slot)
        except AssetValidationError:
            raise
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
            raise AssetValidationError("Gambar tidak dapat dibaca atau rusak") from exc
        except Image.DecompressionBombWarning as exc:
            raise AssetValidationError(
                f"Resolusi gambar melebihi batas {_MAX_PIXELS // 1_000_000} megapiksel"
            ) from exc
    return NormalizedAsset(slot, encoded, SLOT_CONTENT_TYPE[slot], SLOT_EXTENSION[slot])


# --- SVG --------------------------------------------------------------------
_SVG_TAGS = {
    "svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon",
    "defs", "linearGradient", "radialGradient", "stop", "clipPath", "title", "desc",
}
_SVG_ATTRS = {
    "xmlns", "viewBox", "width", "height", "fill", "stroke", "stroke-width",
    "stroke-linecap", "stroke-linejoin", "stroke-miterlimit", "fill-rule",
    "clip-rule", "opacity", "transform", "d", "cx", "cy", "r", "rx", "ry", "x", "y",
    "x1", "y1", "x2", "y2", "points", "offset", "stop-color", "stop-opacity",
    "gradientUnits", "gradientTransform", "id", "clip-path", "preserveAspectRatio",
}
_INTERNAL_URL_RE = re.compile(r"^url\(\s*#[A-Za-z0-9_-]+\s*\)$")


def _normalize_svg(data: bytes) -> NormalizedAsset:
    from defusedxml import ElementTree as SafeET

    try:
        root = SafeET.fromstring(data)
    except Exception as exc:  # defusedxml raises several exception types
        raise AssetValidationError("SVG tidak valid atau memuat entitas berbahaya") from exc

    tag = root.tag.rsplit("}", 1)[-1]
    if tag != "svg":
        raise AssetValidationError("Ornamen harus berupa file SVG")

    for element in root.iter():
        element_tag = element.tag.rsplit("}", 1)[-1]
        if element_tag not in _SVG_TAGS:
            raise AssetValidationError(f"Elemen SVG tidak diizinkan: {element_tag}")
        for name, value in list(element.attrib.items()):
            attr = name.rsplit("}", 1)[-1]
            if attr not in _SVG_ATTRS or attr.lower().startswith("on"):
                raise AssetValidationError(f"Atribut SVG tidak diizinkan: {attr}")
            text = (value or "").strip()
            lowered = text.lower()
            if "javascript:" in lowered or "data:" in lowered:
                raise AssetValidationError("SVG memuat URL yang tidak diizinkan")
            if "url(" in lowered and not _INTERNAL_URL_RE.fullmatch(lowered):
                raise AssetValidationError("SVG hanya boleh merujuk gradient internal")
            if attr in ("href", "src") or "href" in attr:
                raise AssetValidationError("SVG tidak boleh memuat referensi eksternal")
    try:
        cleaned = SafeET.tostring(root, encoding="utf-8", xml_declaration=False)
    except Exception as exc:
        raise AssetValidationError("SVG tidak dapat diserialisasi") from exc
    return NormalizedAsset("ornament", cleaned, SLOT_CONTENT_TYPE["ornament"], "svg")


# --- WOFF2 ------------------------------------------------------------------
def _read_woff2_header(data: bytes) -> tuple[int, int]:
    """Return (declared decoded SFNT size, table count) from the WOFF2 header.

    Layout: signature(4) flavor(4) length(4) numTables(2) reserved(2)
    totalSfntSize(4) totalCompressedSize(4) ...
    """
    if len(data) < 48:
        raise AssetValidationError("Font WOFF2 terlalu pendek")
    num_tables = int.from_bytes(data[12:14], "big")
    total_sfnt_size = int.from_bytes(data[16:20], "big")
    return total_sfnt_size, num_tables


def _normalize_font(data: bytes) -> NormalizedAsset:
    if not data.startswith(b"wOF2"):
        raise AssetValidationError("Font harus berupa file WOFF2")
    declared_size, num_tables = _read_woff2_header(data)
    if num_tables == 0 or num_tables > _MAX_SFNT_TABLES:
        raise AssetValidationError("Font memuat jumlah tabel yang tidak wajar")
    if declared_size <= 0 or declared_size > _MAX_SFNT_BYTES:
        raise AssetValidationError(
            f"Font hasil dekompresi melebihi batas {_MAX_SFNT_BYTES // (1024 * 1024)} MiB"
        )
    try:
        from fontTools.ttLib import TTFont

        font = TTFont(io.BytesIO(data), lazy=False, recalcBBoxes=False, recalcTimestamp=False)
        try:
            if "glyf" not in font and "CFF " not in font and "CFF2" not in font:
                raise AssetValidationError("Font tidak memiliki data glyph yang valid")
            glyph_order = font.getGlyphOrder()
            if len(glyph_order) > 20000:
                raise AssetValidationError("Font memuat terlalu banyak glyph")
        finally:
            font.close()
    except AssetValidationError:
        raise
    except Exception as exc:
        raise AssetValidationError("Font WOFF2 rusak atau tidak dapat dibaca") from exc
    return NormalizedAsset("font", data, SLOT_CONTENT_TYPE["font"], "woff2")


# --- Public API -------------------------------------------------------------
async def validate_and_normalize(slot: str, data: bytes) -> NormalizedAsset:
    if slot not in ASSET_SLOTS:
        raise AssetValidationError("Jenis aset tidak dikenal")
    if not data:
        raise AssetValidationError("File kosong")
    if len(data) > SLOT_INPUT_LIMIT[slot]:
        raise AssetValidationError(
            f"Ukuran file {slot} melebihi batas {SLOT_INPUT_LIMIT[slot] // 1024} KiB"
        )
    if slot == "ornament":
        return _normalize_svg(data)
    if slot == "font":
        return _normalize_font(data)
    return await asyncio.to_thread(_normalize_raster, slot, data)


def _storage_headers(content_type: Optional[str] = None) -> dict[str, str]:
    headers = {"apikey": settings.SUPABASE_SECRET_KEY}
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _object_url(key: str) -> str:
    return (
        f"{settings.SUPABASE_URL.rstrip('/')}/storage/v1/object/"
        f"{settings.SUPABASE_STORAGE_BUCKET}/{key}"
    )


def _public_url(key: str) -> str:
    return (
        f"{settings.SUPABASE_URL.rstrip('/')}/storage/v1/object/public/"
        f"{settings.SUPABASE_STORAGE_BUCKET}/{key}"
    )


def _ensure_storage() -> None:
    if not settings.storage_configured:
        raise StorageUnavailable(
            "Penyimpanan aset belum dikonfigurasi. Hubungi admin untuk mengatur Supabase Storage."
        )


def _client(transport=None) -> httpx.AsyncClient:
    kwargs = {"timeout": httpx.Timeout(20.0), "follow_redirects": False}
    if transport is not None:
        kwargs["transport"] = transport
    return httpx.AsyncClient(**kwargs)


async def upload_asset(
    event_id: str, normalized: NormalizedAsset, transport=None
) -> ManagedAsset:
    _ensure_storage()
    key = f"events/{event_id}/{normalized.slot}-{uuid.uuid4().hex}.{normalized.extension}"
    try:
        async with _client(transport) as client:
            response = await client.post(
                _object_url(key),
                content=normalized.data,
                headers={
                    **_storage_headers(normalized.content_type),
                    "x-upsert": "false",
                    "cache-control": "31536000",
                },
            )
    except httpx.HTTPError as exc:
        raise StorageOperationError("Penyimpanan aset tidak dapat dihubungi") from exc
    if response.status_code not in (200, 201):
        logger.warning("Storage upload rejected with status %s", response.status_code)
        raise StorageOperationError("Penyimpanan aset menolak unggahan")
    return ManagedAsset(
        url=_public_url(key),
        key=key,
        content_type=normalized.content_type,
        size=len(normalized.data),
    )


async def delete_assets(
    urls: list[Optional[str]], event_id: str, transport=None
) -> None:
    """Best-effort cleanup of objects that belong to this event's managed prefix."""
    if not settings.storage_configured:
        return
    keys = []
    for url in urls:
        key = managed_asset_key(url, event_id)
        if key:
            keys.append(key)
    if not keys:
        return
    try:
        async with _client(transport) as client:
            response = await client.request(
                "DELETE",
                f"{settings.SUPABASE_URL.rstrip('/')}/storage/v1/object/{settings.SUPABASE_STORAGE_BUCKET}",
                json={"prefixes": keys},
                headers=_storage_headers("application/json"),
            )
        if response.status_code not in (200, 204):
            logger.warning("Storage delete rejected with status %s", response.status_code)
    except httpx.HTTPError:
        logger.warning("Storage delete failed; orphaned objects remain", exc_info=True)
