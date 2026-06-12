"""Async database engine and session factory for the Model microservice."""

from __future__ import annotations

import logging
import ssl
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)

_engine = None
_async_session_factory = None


def _strip_sslmode(database_url: str) -> tuple[str, dict]:
    """Remove psycopg2-style sslmode from a URL and return asyncpg-compatible connect_args."""
    parsed = urlparse(database_url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    sslmode = (params.pop("sslmode", [None])[0] or "").lower()

    clean_url = urlunparse(parsed._replace(query=urlencode({k: v[0] for k, v in params.items()})))

    connect_args: dict = {}
    if sslmode and sslmode != "disable":
        if sslmode == "verify-full":
            connect_args["ssl"] = ssl.create_default_context()
        else:
            # require / verify-ca / prefer — require SSL but skip hostname verification
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            connect_args["ssl"] = ctx

    return clean_url, connect_args


async def init_db(database_url: str) -> None:
    """Initialise the async engine.  Does NOT call create_all — Alembic owns the schema."""
    global _engine, _async_session_factory  # noqa: PLW0603
    clean_url, connect_args = _strip_sslmode(database_url)
    _engine = create_async_engine(clean_url, echo=False, pool_pre_ping=True, connect_args=connect_args)
    _async_session_factory = sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)
    logger.info("Database engine initialised")


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async session."""
    if _async_session_factory is None:
        msg = "Database not initialised. Set MODEL_SERVICE_DATABASE_URL in .env."
        raise RuntimeError(msg)
    async with _async_session_factory() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    """Context manager for model-service database work outside FastAPI dependencies."""
    if _async_session_factory is None:
        msg = "Database not initialised. Set MODEL_SERVICE_DATABASE_URL in .env."
        raise RuntimeError(msg)
    async with _async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
