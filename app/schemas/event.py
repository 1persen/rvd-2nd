"""Event schemas."""
from datetime import datetime
from typing import Optional

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.enums.event_status import EventStatus


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


class EventUpdate(EventBase):
    status: Optional[EventStatus] = None
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    slug: Optional[str] = Field(default=None, max_length=100)


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
