"""
Database session management (SQLAlchemy 2.0 async).

Production: PostgreSQL via asyncpg (DATABASE_URL=postgresql+asyncpg://...).
Dev/test fallback: SQLite via aiosqlite (DATABASE_URL=sqlite+aiosqlite:///...).
All repositories depend on app.core.database.get_db — never import the
engine directly in business code.

Schema is managed exclusively by Alembic migrations. No create_all().
"""
from collections.abc import AsyncGenerator
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


# Normalize the runtime DB URL to an async driver. Supabase/Vercel may provide
# a plain `postgresql://` (or psycopg2) URL; the async engine requires asyncpg.
_db_url = settings.DATABASE_URL
if _db_url.startswith("postgresql://"):
    _db_url = _db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
elif _db_url.startswith("postgresql+psycopg2://"):
    _db_url = _db_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


# Diagnostic startup log — logs driver type and URL source without exposing credentials.
_driver = settings.DATABASE_URL.split("://", 1)[0] if "://" in settings.DATABASE_URL else "unknown"
_host = ""
try:
    from urllib.parse import urlsplit
    parts = urlsplit(settings.DATABASE_URL)
    _host = parts.hostname or ""
    _port = parts.port or ""
except Exception:
    pass
logging.getLogger("app.core.database").warning(
    "DB diag: driver=%s host=%s port=%s is_sqlite=%s",
    _driver, _host, _port, settings.is_sqlite,
)

# SQLite uses NullPool, which rejects the queue-pool sizing arguments.
# Supabase transaction pooler (PgBouncer) does not support prepared statements,
# so disable asyncpg's statement cache when running against it.
_is_postgres = _db_url.startswith("postgresql")
_connect_args = {
    "timeout": 10,  # connect timeout
    "command_timeout": settings.DB_STATEMENT_TIMEOUT_SECONDS,
    "server_settings": {
        "statement_timeout": str(settings.DB_STATEMENT_TIMEOUT_SECONDS * 1000),
        # A crashed request must not leave a row lock held forever.
        "idle_in_transaction_session_timeout": "30000",
    },
}
if _is_postgres:
    # Required for Supabase/Upstash-style transaction poolers (PgBouncer).
    _connect_args["statement_cache_size"] = 0
    _connect_args["prepared_statement_cache_size"] = 0

_pool_sizing = {} if settings.is_sqlite else {
    "pool_size": settings.DB_POOL_SIZE,
    "max_overflow": settings.DB_MAX_OVERFLOW,
    "pool_timeout": settings.DB_POOL_TIMEOUT,
    "pool_recycle": 1800,
    "pool_pre_ping": True,
    "connect_args": _connect_args,
}

engine = create_async_engine(
    _db_url,
    echo=settings.DEBUG and not settings.is_sqlite,
    **_pool_sizing,
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields a scoped async session."""
    async with async_session() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
