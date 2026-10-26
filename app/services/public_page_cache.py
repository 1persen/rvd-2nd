"""Five-second, bounded cache for anonymous discovery HTML, never checkout/API data."""
import asyncio
import time
from collections import OrderedDict
from functools import wraps

from fastapi.responses import HTMLResponse

TTL_SECONDS = 5
MAX_PAGES = 64
_pages = OrderedDict()
_lock = asyncio.Lock()


def cache_discovery_page(render):
    @wraps(render)
    async def cached(*args, **kwargs):
        request = kwargs.get("request") or args[0]
        key = (request.url.path, kwargs.get("event_id"))
        # Coalesce cold requests per worker rather than queuing a DB read per visitor.
        async with _lock:
            entry = _pages.get(key)
            if entry and entry[0] > time.monotonic():
                _pages.move_to_end(key)
                return HTMLResponse(entry[1])
            response = await render(*args, **kwargs)
            if response.status_code == 200:
                _pages[key] = (time.monotonic() + TTL_SECONDS, response.body)
                _pages.move_to_end(key)
                while len(_pages) > MAX_PAGES:
                    _pages.popitem(last=False)
            return response

    return cached


async def invalidate_discovery_pages() -> None:
    """Drop rendered discovery HTML after event presentation/visibility changes."""
    async with _lock:
        _pages.clear()
