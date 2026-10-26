"""Admin event save/preview orchestration around asset uploads.

A Save is one request: validate every file, upload new objects, persist metadata,
then clean up replaced objects. Preview reuses the same validation/normalization
but never touches Storage or the database.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.event import Event
from app.schemas.event import EventUpdate, ThemeConfig
from app.services import event_asset_service, event_page_service, event_service
from app.services.event_asset_service import (
    ASSET_SLOTS,
    AssetValidationError,
    ManagedAsset,
    NormalizedAsset,
    StorageUnavailable,
)

logger = logging.getLogger(__name__)


@dataclass
class SaveResult:
    event: Event
    uploaded: list[ManagedAsset] = field(default_factory=list)
    removed_urls: list[str] = field(default_factory=list)


async def read_upload(file: UploadFile, slot: str) -> bytes:
    limit = event_asset_service.SLOT_INPUT_LIMIT[slot]
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise AssetValidationError(
            f"Ukuran file {slot} melebihi batas {limit // 1024} KiB"
        )
    return data


async def normalize_files(files: dict[str, UploadFile]) -> dict[str, NormalizedAsset]:
    normalized: dict[str, NormalizedAsset] = {}
    for slot in ASSET_SLOTS:
        upload = files.get(slot)
        if upload is None:
            continue
        data = await read_upload(upload, slot)
        normalized[slot] = await event_asset_service.validate_and_normalize(slot, data)
    return normalized


def _theme_from_payload(event: Event, payload: EventUpdate) -> ThemeConfig:
    if payload.theme_config is not None:
        return payload.theme_config
    return ThemeConfig.model_validate(
        event_page_service.normalize_theme_config(event.theme_config)
    )


async def save_event_with_assets(
    session: AsyncSession,
    event_id: str,
    payload: EventUpdate,
    files: dict[str, UploadFile],
) -> SaveResult:
    event = await event_service.get_event_or_404(session, event_id)
    if files and not settings.storage_configured:
        raise StorageUnavailable(
            "Penyimpanan aset belum dikonfigurasi. Simpan tema tanpa file atau atur Supabase Storage."
        )

    # Validate everything before the first upload so a bad file cannot leave
    # half of a form's objects behind.
    normalized = await normalize_files(files)
    theme = _theme_from_payload(event, payload)
    theme_data = theme.model_dump()
    old_theme = event_page_service.normalize_theme_config(event.theme_config)
    old_assets = dict(old_theme.get("assets") or {})

    uploaded: list[ManagedAsset] = []
    try:
        for slot, asset in normalized.items():
            managed = await event_asset_service.upload_asset(event_id, asset)
            uploaded.append(managed)
            theme_data["assets"][slot] = managed.url
        # Rebuild from the explicit fields so theme_config is marked as set even
        # when the form only changed a file (otherwise exclude_unset drops it).
        updated_payload = EventUpdate.model_validate({
            **payload.model_dump(exclude_unset=True),
            "theme_config": ThemeConfig.model_validate(theme_data).model_dump(),
        })
        event = await event_service.update_event(session, event_id, updated_payload)
    except Exception:
        await event_asset_service.delete_assets([asset.url for asset in uploaded], event_id)
        raise

    new_assets = event_page_service.normalize_theme_config(event.theme_config).get("assets") or {}
    removed = [
        url for slot, url in old_assets.items()
        if url and url not in {value for value in new_assets.values() if value}
    ]
    return SaveResult(event=event, uploaded=uploaded, removed_urls=removed)


async def build_preview(
    session: AsyncSession,
    payload: EventUpdate,
    files: dict[str, UploadFile],
    event_id: Optional[str],
) -> tuple[object, dict, dict]:
    """Return (transient_event, render_context, overrides) without any writes."""
    existing = await event_service.get_event_or_404(session, event_id) if event_id else None
    normalized = await normalize_files(files)
    overrides = {
        slot: asset.data_url() for slot, asset in normalized.items()
    }

    merged_data = payload.model_dump(exclude_unset=True)
    if existing is not None:
        from app.schemas.event import AdminEventOut

        base = AdminEventOut.model_validate(existing).model_dump()
        merged_data = {**base, **merged_data}
    merged = EventUpdate.model_validate(merged_data)
    transient = event_page_service.build_preview_event(merged, event_id=event_id)

    collections = {"peserta": [], "klasemen": [], "sultan": []}
    total_teams = total_votes = total_supporters = 0
    if existing is not None:
        from app.repositories import payment as payment_repo
        from app.repositories import team as team_repo
        from app.schemas.team import TeamOut
        from app.services import leaderboard_service

        pairs = await team_repo.get_teams_with_votes(session, existing.id)
        teams = []
        for rank, (team, votes) in enumerate(pairs, start=1):
            item = TeamOut.model_validate(team).model_dump()
            item.update({"votes": votes, "rank": rank})
            teams.append(item)
        collections["peserta"] = teams

        ranking = await leaderboard_service.get_leaderboard(session, existing.id)
        collections["klasemen"] = ranking["entries"]
        collections["sultan"] = (await event_service.build_donors(session, existing.id))["entries"]

        stats = (await team_repo.stats_by_event(session, [existing.id])).get(existing.id, (0, 0))
        total_teams, total_votes = stats
        donor_counts = await payment_repo.count_donors_by_event(session, [existing.id])
        total_supporters = donor_counts.get(existing.id, 0)

    context = event_page_service.build_invite_context(
        transient,
        total_teams=total_teams,
        total_votes=total_votes,
        total_supporters=total_supporters,
        preview=True,
        collections=collections,
        asset_overrides=overrides,
    )
    return transient, context, overrides
