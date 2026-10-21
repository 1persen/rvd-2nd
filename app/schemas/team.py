"""Team schemas."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class TeamBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    school: Optional[str] = None
    education_level: Optional[str] = None
    description: Optional[str] = None
    logo_url: Optional[str] = None
    sort_order: int = 0


class TeamCreate(TeamBase):
    event_id: Optional[str] = None


class TeamUpdate(TeamBase):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    school: Optional[str] = None
    education_level: Optional[str] = None
    description: Optional[str] = None
    logo_url: Optional[str] = None
    sort_order: Optional[int] = None


class TeamOut(TeamBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_id: str
    total_votes: int = 0
    supporter_count: int = 0
    current_rank: Optional[int] = None
    education_level: Optional[str] = None
    is_active: bool = True
    created_at: datetime
    updated_at: datetime


class TeamWithVotes(TeamOut):
    votes: int = 0
    rank: int = 0
