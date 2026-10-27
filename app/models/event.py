"""
Event model — one row per competition/event.
Status lifecycle: Draft → Published → Voting Open → Voting Closed → Finished → Archived.
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Enum, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.enums.event_status import EventStatus
from app.models.mixins import SoftDeleteMixin, TimestampMixin, UUIDMixin
from app.utils import new_id


class Event(TimestampMixin, SoftDeleteMixin, UUIDMixin, Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=lambda: new_id("evt")
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    slug: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, unique=True, index=True)
    category: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    banner_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # Presentation is additive: old rows get the classic template and {} theme.
    # SQLite dev databases keep working through the JSON variant.
    template_key: Mapped[str] = mapped_column(
        String(20), nullable=False, default="classic", server_default="classic"
    )
    theme_config: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=False,
        default=dict,
        server_default="{}",
    )
    timezone: Mapped[str] = mapped_column(String(50), default="UTC", nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="IDR", nullable=False)
    price_per_vote: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_vote_per_transaction: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[EventStatus] = mapped_column(
        Enum(EventStatus, name="event_status", create_type=False),
        default=EventStatus.DRAFT,
        nullable=False,
        index=True,
    )
    opens_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    closes_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    teams: Mapped[list["Team"]] = relationship(  # noqa: F821
        back_populates="event", cascade="all, delete-orphan"
    )

    def _is_within_voting_window(self, now: datetime) -> bool:
        if self.opens_at and now < self.opens_at:
            return False
        if self.closes_at and now > self.closes_at:
            return False
        return True

    @property
    def is_voting_open(self) -> bool:
        now = datetime.now(timezone.utc)
        if self.status == EventStatus.VOTING_OPEN:
            return self._is_within_voting_window(now)
        if self.opens_at is not None or self.closes_at is not None:
            return self._is_within_voting_window(now)
        return False
