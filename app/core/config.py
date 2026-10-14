"""
Application configuration.
Loads settings from environment variables (.env).
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "Web Voting Championship"
    APP_VERSION: str = "1.0.0"
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    # DEBUG also turns on SQL echo; never enable it in production.
    DEBUG: bool = False

    # postgresql+asyncpg://... untuk production.
    # sqlite+aiosqlite:///./web_voting.db untuk dev/test tanpa Postgres.
    DATABASE_URL: str = "postgresql+asyncpg://user:password@localhost:5432/web_voting"
    # Per worker process. Budget: workers x (POOL_SIZE + MAX_OVERFLOW) must stay
    # well below Postgres max_connections (2 x 10 = 20 of the default 100).
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 5
    DB_POOL_TIMEOUT: int = 10
    DB_STATEMENT_TIMEOUT_SECONDS: int = 15

    # Requests per minute per client IP. Venue Wi-Fi and mobile carrier CGNAT put
    # many voters behind one address, so these are sized for ~200 voters sharing
    # one IP; lower them if abuse matters more than that case.
    # Load test: 200 voters behind one IP make ~1600 public requests in 20 s.
    RATE_LIMIT_PUBLIC_PER_MIN: int = 3000
    RATE_LIMIT_PAYMENT_PER_MIN: int = 200
    RATE_LIMIT_AUTH_PER_MIN: int = 10
    RATE_LIMIT_WEBHOOK_PER_MIN: int = 600

    # SSE pushes for one event are coalesced into at most one per window, so a
    # burst of payments costs one rebuild + one push instead of one per payment.
    # 0 pushes inline after every vote (tests).
    LEADERBOARD_PUSH_INTERVAL_SECONDS: float = 1.0
    SECRET_KEY: str = "change-this-secret-key"

    MIDTRANS_SERVER_KEY: str = ""
    MIDTRANS_CLIENT_KEY: str = ""
    MIDTRANS_IS_PRODUCTION: bool = False
    # Explicit opt-in for the fake QRIS + unsigned webhook flow (local dev only).
    # An empty MIDTRANS_SERVER_KEY alone never enables it.
    MIDTRANS_MOCK_MODE: bool = False
    APP_BASE_URL: str = "http://localhost:8000"

    # Duitku payment gateway (Sandbox only per spec)
    DUITKU_MERCHANT_CODE: str = ""
    DUITKU_API_KEY: str = ""
    DUITKU_ENVIRONMENT: str = "sandbox"  # sandbox | production (not used yet)

    REDIS_URL: str = "redis://localhost:6379/0"
    CLOUDFLARE_API_KEY: str = ""
    MAX_ACTIVE_USER: int = 500

    JWT_SECRET_KEY: str = "change-this-secret-key"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 15

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        for name in ("SECRET_KEY", "JWT_SECRET_KEY"):
            if getattr(self, name) in ("", "change-this-secret-key"):
                raise RuntimeError(f"{name} must be set to a secure value (e.g. `openssl rand -hex 32`)")
        if self.MIDTRANS_MOCK_MODE and (self.MIDTRANS_SERVER_KEY or self.MIDTRANS_IS_PRODUCTION):
            raise RuntimeError("MIDTRANS_MOCK_MODE cannot be combined with a Midtrans server key or production mode")

    @property
    def midtrans_mock_enabled(self) -> bool:
        return self.MIDTRANS_MOCK_MODE and not self.MIDTRANS_SERVER_KEY

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")

    @property
    def is_redis_available(self) -> bool:
        return bool(self.REDIS_URL) and not self.REDIS_URL.startswith("memory://")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

# Diagnostic startup log — logs driver type and source without exposing credentials.
import logging as _logging
_driver = settings.DATABASE_URL.split("://", 1)[0] if "://" in settings.DATABASE_URL else "unknown"
_logging.getLogger("app.core.config").warning(
    "DB diag: driver=%s is_sqlite=%s",
    _driver, settings.is_sqlite,
)
