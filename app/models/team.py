"""
Team model — contestant/participant belonging to an event.
Soft-delete only; vote history must always remain intact.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import SoftDeleteMixin, TimestampMixin, UUIDMixin
from app.utils import new_id


class Team(TimestampMixin, SoftDeleteMixin, UUIDMixin, Base):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=lambda: new_id("tm")
    )
    event_id: Mapped[str] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    school: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    education_level: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    logo_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_votes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    supporter_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Rank before the latest vote; current_rank vs previous_rank is the trend arrow.
    previous_rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    event: Mapped["Event"] = relationship(back_populates="teams")  # noqa: F821
    payments: Mapped[list["Payment"]] = relationship(back_populates="team")  # noqa: F821
    vote_logs: Mapped[list["VoteLog"]] = relationship(back_populates="team")  # noqa: F821
