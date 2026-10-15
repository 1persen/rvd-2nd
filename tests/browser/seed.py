"""Disposable database fixtures for real browser flows; never use on production."""
import asyncio
import json
import secrets
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.core.config import settings
from app.core.database import async_session
from app.models.event import Event, EventStatus
from app.models.team import Team
from app.seeds.admin import seed_admin

if not Path(urlsplit(settings.DATABASE_URL).path).name.endswith('test'):
    raise SystemExit('Browser fixtures require a disposable database ending in test')


async def main():
    password = secrets.token_urlsafe(32)
    email = f'browser-{secrets.token_hex(6)}@test.local'
    await seed_admin(email, password)
    async with async_session() as session:
        event = Event(name='Festival Talenta Nusantara', status=EventStatus.VOTING_OPEN,
                      price_per_vote=1000, description='Ruang untuk setiap talenta dan dukungan.')
        session.add(event)
        await session.flush()
        teams = [Team(event_id=event.id, name=name, school='Komunitas Kreatif', total_votes=votes)
                 for name, votes in [('Cakrawala', 1200), ('Arunika', 950), ('Lentera', 720), ('Aksara', 410)]]
        session.add_all(teams)
        await session.commit()
        print(json.dumps(dict(event_id=event.id, team_id=teams[0].id, email=email, password=password)))


asyncio.run(main())
