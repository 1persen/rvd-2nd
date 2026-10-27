"""
Web Voting Championship — application entrypoint.

Semua layer aktif: PostgreSQL/SQLite (SQLAlchemy async), repository,
service, API /api/v1, halaman SSR, Midtrans webhook, SSE leaderboard,
rate limiter, audit log.

Schema database dikelola oleh Alembic. Seeding dilakukan via CLI:
    python -m app.seeds run demo
    python -m app.seeds run admin
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.middleware import (  # noqa: F401
    AuditMiddleware,
    BodyLimitMiddleware,
    RateLimitMiddleware,
    SecurityMiddleware,
)
from app.routers import admin, auth, events, leaderboard_stream, pages, payments

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
from app.middleware.logging import LoggingMiddleware
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Database ready; app %s v%s", settings.APP_NAME, settings.APP_VERSION)
    yield


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    debug=settings.DEBUG,
    lifespan=lifespan,
)

# Static assets
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Middleware — urutan penting. Middleware yang ditambahkan terakhir akan
# dijalankan pertama (paling luar).
# Logging -> Body Limit -> Security -> Rate Limit -> Audit -> Route
# (Body Limit sits outside routing so an oversized admin upload answers 413
# before multipart parsing; Security wraps Rate Limit so 429 responses carry
# the security headers too.)
app.add_middleware(AuditMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(SecurityMiddleware)
app.add_middleware(BodyLimitMiddleware)
app.add_middleware(LoggingMiddleware)

# Routers
app.include_router(admin.router)
app.include_router(auth.router)
app.include_router(events.router)
app.include_router(leaderboard_stream.router)
app.include_router(payments.router)
app.include_router(pages.router)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler_custom(request: Request, exc: StarletteHTTPException):
    """Envelope {success, message, data} untuk API; halaman tetap dihandle
    oleh handler default FastAPI (HTML) di luar /api/."""
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=exc.status_code,
            content={"success": False, "message": exc.detail, "data": None},
        )
    return await http_exception_handler(request, exc)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "message": "Data yang dikirim tidak valid",
            "data": exc.errors(),
        },
    )


@app.get("/health")
async def health():
    return {
        "success": True,
        "message": "ok",
        "data": {"app": settings.APP_NAME, "version": settings.APP_VERSION},
    }
