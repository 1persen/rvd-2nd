"""Invite template registry, theme normalization, and render context builders.

This module is the single source of truth for how an event becomes a public
page. Routers never build template paths from request or database strings: the
registry maps a validated key to a fixed template, and unknown values fall back
to the classic layout with a warning.
"""
from __future__ import annotations

import copy
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import ValidationError

from app.core.config import settings
from app.enums.education_level import EDUCATION_LABELS
from app.enums.event_status import EventStatus
from app.schemas.event import ThemeConfig

logger = logging.getLogger(__name__)

DEFAULT_TEMPLATE = "classic"
INVITE_TEMPLATES: dict[str, str] = {
    "classic": "pages/invite/classic.html",
    "poster": "pages/invite/poster.html",
    "cinematic": "pages/invite/cinematic.html",
}

_STATUS_LABELS = {
    EventStatus.DRAFT: "Draf",
    EventStatus.PUBLISHED: "Terbit",
    EventStatus.VOTING_OPEN: "Voting dibuka",
    EventStatus.VOTING_CLOSED: "Voting ditutup",
    EventStatus.FINISHED: "Selesai",
    EventStatus.ARCHIVED: "Diarsipkan",
}

_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_SAFE_URL_RE = re.compile(r"^https?://[^\s'\"()<>\\]+$")
_MANAGED_KEY_RE = re.compile(
    r"^events/(?P<event>[A-Za-z0-9_-]+)/(?P<slot>background|logo|poster|ornament|font)-"
    r"(?P<uuid>[0-9a-f]{32})\.(?P<ext>webp|svg|woff2)$"
)

DEFAULT_THEME: dict[str, Any] = {
    "background_type": "default",
    "background_color": "#160F0A",
    "gradient_start": "#160F0A",
    "gradient_end": "#3A270F",
    "background_overlay": 0.45,
    "primary_color": "#D4A036",
    "secondary_color": "#160F0A",
    "text_color": "#FDFAF5",
    "button_text_color": "#160F0A",
    "assets": {
        "background": None,
        "logo": None,
        "poster": None,
        "ornament": None,
        "font": None,
    },
    "show_countdown": True,
    "show_participants": True,
}

_THEME_FIELDS = tuple(k for k in DEFAULT_THEME if k != "assets")
_ASSET_SLOTS = ("background", "logo", "poster", "ornament", "font")


# --- Theme normalization ----------------------------------------------------
def default_theme() -> dict:
    return copy.deepcopy(DEFAULT_THEME)


def normalize_theme_config(raw: Any) -> dict:
    """Return a safe v1 theme.

    Admin writes are validated strictly before they reach this function. Public
    reads may encounter legacy/hand-edited JSON, so unsafe or unknown values are
    replaced with defaults instead of raising a 500.
    """
    if not isinstance(raw, dict):
        return default_theme()
    try:
        return ThemeConfig.model_validate(raw).model_dump()
    except ValidationError:
        pass

    theme = default_theme()
    for field in _THEME_FIELDS:
        if field not in raw:
            continue
        value = raw[field]
        if field in {
            "background_color",
            "gradient_start",
            "gradient_end",
            "primary_color",
            "secondary_color",
            "text_color",
            "button_text_color",
        }:
            if isinstance(value, str) and _HEX_RE.fullmatch(value.strip()):
                theme[field] = value.strip().upper()
        elif field == "background_type":
            if value in ("default", "solid", "gradient", "image"):
                theme[field] = value
        elif field == "background_overlay":
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                theme[field] = min(0.85, max(0.0, float(value)))
        elif field in ("show_countdown", "show_participants"):
            if isinstance(value, bool):
                theme[field] = value

    raw_assets = raw.get("assets")
    if isinstance(raw_assets, dict):
        for slot in _ASSET_SLOTS:
            value = raw_assets.get(slot)
            if isinstance(value, str) and _SAFE_URL_RE.fullmatch(value.strip()):
                theme["assets"][slot] = value.strip()
    return theme


def validate_theme_config(theme: ThemeConfig) -> dict:
    """Strict write validation: colors must be readable, assets must be ours."""
    data = theme.model_dump()
    _validate_contrast(data)
    return data


def _channel(value: int) -> float:
    value = value / 255
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def _luminance(hex_color: str) -> float:
    raw = hex_color.lstrip("#")
    r, g, b = (int(raw[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast_ratio(first: str, second: str) -> float:
    light, dark = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def _validate_contrast(theme: dict) -> None:
    text = theme["text_color"]
    checks = [
        ("text_color terhadap background_color", text, theme["background_color"], 4.5),
        ("text_color terhadap gradient_start", text, theme["gradient_start"], 4.5),
        ("text_color terhadap gradient_end", text, theme["gradient_end"], 4.5),
        ("button_text_color terhadap primary_color", theme["button_text_color"], theme["primary_color"], 4.5),
        ("primary_color terhadap secondary_color", theme["primary_color"], theme["secondary_color"], 3.0),
    ]
    for label, first, second, minimum in checks:
        ratio = contrast_ratio(first, second)
        if ratio < minimum:
            raise ValueError(
                f"Kontras {label} terlalu rendah ({ratio:.2f}:1, minimal {minimum}:1)"
            )


# --- Template + asset resolution -------------------------------------------
def resolve_template_key(value: Optional[str]) -> str:
    key = (value or "").strip().lower()
    if key in INVITE_TEMPLATES:
        return key
    if key:
        logger.warning("Unknown invite template key %r; falling back to classic", value)
    return DEFAULT_TEMPLATE


def template_path(value: Optional[str]) -> str:
    return INVITE_TEMPLATES[resolve_template_key(value)]


def _safe_url(value: Optional[str]) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if _SAFE_URL_RE.fullmatch(value) else None


def managed_asset_key(url: Optional[str], event_id: str) -> Optional[str]:
    """Return the Storage object key when ``url`` is a managed asset of this event."""
    if not isinstance(url, str) or not settings.storage_configured:
        return None
    base = f"{settings.SUPABASE_URL.rstrip('/')}/storage/v1/object/public/{settings.SUPABASE_STORAGE_BUCKET}/"
    if not url.startswith(base):
        return None
    key = url[len(base):]
    match = _MANAGED_KEY_RE.fullmatch(key)
    if not match or match.group("event") != event_id:
        return None
    return key


def is_managed_asset_url(url: Optional[str], event_id: str) -> bool:
    return managed_asset_key(url, event_id) is not None


def resolve_assets(event, theme: dict, overrides: Optional[dict] = None) -> dict:
    """Pick the asset for each slot, with explicit legacy fallbacks.

    ``overrides`` is preview-only: normalized uploads become data URLs so the
    unsaved page can render them without writing to Storage.
    """
    raw = theme.get("assets") or {}
    overrides = overrides or {}
    banner = _safe_url(getattr(event, "banner_url", None))
    logo = overrides.get("logo") or _safe_url(raw.get("logo")) or banner or "/static/images/raja-voting-logo.png"
    poster = overrides.get("poster") or _safe_url(raw.get("poster")) or banner or logo
    background = overrides.get("background") or _safe_url(raw.get("background"))
    if theme.get("background_type") == "image" and not background:
        background = banner
    return {
        "background": background,
        "logo": logo,
        "poster": poster,
        "ornament": overrides.get("ornament") or _safe_url(raw.get("ornament")),
        "font": overrides.get("font") or _safe_url(raw.get("font")),
    }


def build_theme_css(theme: dict, assets: dict) -> str:
    """Build CSS from already-validated tokens; never from arbitrary strings."""
    font_family = "'RVDEventCustom', 'Poppins', 'DM Sans', sans-serif" if assets.get("font") else ""
    declarations = [
        f"--event-primary:{theme['primary_color']};",
        f"--event-secondary:{theme['secondary_color']};",
        f"--event-text:{theme['text_color']};",
        f"--event-button-text:{theme['button_text_color']};",
        f"--event-overlay:{theme['background_overlay']:.2f};",
    ]
    if font_family:
        declarations.append(f"--event-font:{font_family};")

    rules = [f".event-page{{{''.join(declarations)}}}"]
    background_type = theme["background_type"]
    if background_type == "solid":
        rules.append(
            f'.event-page[data-bg="solid"]{{background-color:{theme["background_color"]};}}'
        )
    elif background_type == "gradient":
        rules.append(
            '.event-page[data-bg="gradient"]{background-image:'
            f'linear-gradient(135deg,{theme["gradient_start"]},{theme["gradient_end"]});}}'
        )
    elif background_type == "image" and assets.get("background"):
        overlay = theme["background_overlay"]
        rules.append(
            '.event-page[data-bg="image"]{background-image:'
            f'linear-gradient(rgba(0,0,0,{overlay:.2f}),rgba(0,0,0,{overlay:.2f})),'
            f"url('{assets['background']}');background-size:cover;"
            "background-position:center;background-attachment:scroll;}"
        )
    if assets.get("font"):
        rules.append(
            "@font-face{font-family:'RVDEventCustom';"
            f"src:url('{assets['font']}') format('woff2');font-weight:100 900;"
            "font-style:normal;font-display:swap;}"
        )
    return "\n".join(rules)


# --- Event payload ----------------------------------------------------------
def event_dict(
    event,
    *,
    total_teams: int = 0,
    total_votes: int = 0,
    total_supporters: int = 0,
    progress: int = 0,
) -> dict:
    is_voting_open = bool(event.is_voting_open)
    status = _STATUS_LABELS.get(event.status, event.status.value)
    if event.status == EventStatus.VOTING_OPEN and not is_voting_open:
        status = "Voting ditutup"
        if event.opens_at and event.opens_at > datetime.now(timezone.utc):
            status = "Voting belum dimulai"

    template_key = resolve_template_key(getattr(event, "template_key", DEFAULT_TEMPLATE))
    theme = normalize_theme_config(getattr(event, "theme_config", None))
    category = getattr(event, "category", None)
    return {
        "id": event.id,
        "name": event.name,
        "slug": getattr(event, "slug", None),
        "invite_path": f"/invite/{getattr(event, 'slug', None) or event.id}",
        "status": status,
        "status_code": event.status.value,
        "is_voting_open": is_voting_open,
        "description": event.description or "",
        "banner_url": _safe_url(getattr(event, "banner_url", None)),
        "opens_at": event.opens_at.isoformat() if event.opens_at else None,
        "closes_at": event.closes_at.isoformat() if event.closes_at else None,
        "price_per_vote": getattr(event, "price_per_vote", 10000) or 10000,
        "category": category,
        "category_label": EDUCATION_LABELS.get(category, ""),
        "template_key": template_key,
        "theme": theme,
        "total_teams": total_teams,
        "total_votes": total_votes,
        "total_supporters": total_supporters,
        "progress": progress,
    }


def build_invite_context(
    event,
    *,
    total_teams: int = 0,
    total_votes: int = 0,
    total_supporters: int = 0,
    preview: bool = False,
    collections: Optional[dict] = None,
    asset_overrides: Optional[dict] = None,
) -> dict:
    template_key = resolve_template_key(getattr(event, "template_key", DEFAULT_TEMPLATE))
    theme = normalize_theme_config(getattr(event, "theme_config", None))
    assets = resolve_assets(event, theme, asset_overrides)
    payload = event_dict(
        event,
        total_teams=total_teams,
        total_votes=total_votes,
        total_supporters=total_supporters,
    )
    return {
        "event": payload,
        "all_events": [payload],
        "template_key": template_key,
        "template_path": INVITE_TEMPLATES[template_key],
        "theme": theme,
        "assets": assets,
        "theme_css": build_theme_css(theme, assets),
        "preview": bool(preview),
        "invite_config": {
            "preview": bool(preview),
            "collections": collections or {},
        },
    }


def build_preview_event(payload, event_id: Optional[str] = None):
    """Transient Event for a not-yet-saved form; never added to a session."""
    from app.models.event import Event

    now = datetime.now(timezone.utc)
    return Event(
        id=event_id or "evt_preview",
        name=payload.name or "Event baru",
        description=payload.description,
        slug=payload.slug or "preview",
        category=payload.category,
        banner_url=payload.banner_url,
        template_key=payload.template_key or DEFAULT_TEMPLATE,
        theme_config=payload.theme_config.model_dump() if payload.theme_config else default_theme(),
        timezone=payload.timezone or "UTC",
        currency=payload.currency or "IDR",
        price_per_vote=payload.price_per_vote,
        max_vote_per_transaction=payload.max_vote_per_transaction,
        status=payload.status or EventStatus.DRAFT,
        opens_at=payload.opens_at,
        closes_at=payload.closes_at,
        created_at=now,
        updated_at=now,
    )
