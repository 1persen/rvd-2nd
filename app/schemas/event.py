"""Event schemas."""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.enums.event_status import EventStatus

HexColor = str
AssetSlot = Literal["background", "logo", "poster", "ornament", "font"]
BackgroundType = Literal["default", "solid", "gradient", "image"]
TemplateKey = Literal["classic", "poster", "cinematic"]

_HEX_RE = r"^#[0-9A-Fa-f]{6}$"


class ThemeAssets(BaseModel):
    """Server-managed asset URLs. Clients never set arbitrary values here."""

    model_config = ConfigDict(extra="forbid")

    background: Optional[str] = Field(default=None, max_length=500)
    logo: Optional[str] = Field(default=None, max_length=500)
    poster: Optional[str] = Field(default=None, max_length=500)
    ornament: Optional[str] = Field(default=None, max_length=500)
    font: Optional[str] = Field(default=None, max_length=500)


class ThemeConfig(BaseModel):
    """Strict v1 theme contract. Unknown keys and raw CSS are rejected."""

    model_config = ConfigDict(extra="forbid")

    background_type: BackgroundType = "default"
    background_color: HexColor = Field(default="#160F0A", pattern=_HEX_RE)
    gradient_start: HexColor = Field(default="#160F0A", pattern=_HEX_RE)
    gradient_end: HexColor = Field(default="#3A270F", pattern=_HEX_RE)
    background_overlay: float = Field(default=0.45, ge=0.0, le=0.85)
    primary_color: HexColor = Field(default="#D4A036", pattern=_HEX_RE)
    secondary_color: HexColor = Field(default="#160F0A", pattern=_HEX_RE)
    text_color: HexColor = Field(default="#FDFAF5", pattern=_HEX_RE)
    button_text_color: HexColor = Field(default="#160F0A", pattern=_HEX_RE)
    assets: ThemeAssets = Field(default_factory=ThemeAssets)
    show_countdown: bool = True
    show_participants: bool = True

    @field_validator("background_color", "gradient_start", "gradient_end",
                     "primary_color", "secondary_color", "text_color",
                     "button_text_color", mode="before")
    @classmethod
    def _normalize_hex(cls, value):
        if isinstance(value, str):
            return value.strip().upper()
        return value


class EventBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None
    slug: Optional[str] = Field(default=None, max_length=100)
    category: Optional[Literal["sma-smk-ma", "smp-mts", "sd-mi", "purna-klub"]] = None
    banner_url: Optional[str] = Field(default=None, max_length=500)
    timezone: str = Field(default="UTC", max_length=50)
    currency: str = Field(default="IDR", max_length=3)
    price_per_vote: Optional[int] = None
    max_vote_per_transaction: Optional[int] = None
    opens_at: Optional[datetime] = None
    closes_at: Optional[datetime] = None


class EventCreate(EventBase):
    status: EventStatus = EventStatus.DRAFT
    template_key: TemplateKey = "classic"
    theme_config: ThemeConfig = Field(default_factory=ThemeConfig)


class EventUpdate(EventBase):
    status: Optional[EventStatus] = None
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    slug: Optional[str] = Field(default=None, max_length=100)
    template_key: Optional[TemplateKey] = None
    theme_config: Optional[ThemeConfig] = None


class EventOut(EventBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: EventStatus
    created_at: datetime
    updated_at: datetime

    @property
    def status_label(self) -> str:
        return {
            EventStatus.DRAFT: "Draft",
            EventStatus.PUBLISHED: "Published",
            EventStatus.VOTING_OPEN: "Voting Open",
            EventStatus.VOTING_CLOSED: "Voting Closed",
            EventStatus.FINISHED: "Finished",
            EventStatus.ARCHIVED: "Archived",
        }.get(self.status, self.status.value)


class AdminEventOut(EventOut):
    """Admin read model; legacy/invalid theme data is normalized by the route."""

    template_key: str = "classic"
    theme_config: dict = Field(default_factory=dict)


class EventPreviewPayload(EventUpdate):
    """Preview may omit everything except the fields currently being edited."""

    event_id: Optional[str] = None
