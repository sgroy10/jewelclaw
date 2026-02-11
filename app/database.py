"""
Database connection and session management.
Supports PostgreSQL (production) and SQLite (development/testing).
Uses SQLAlchemy async for non-blocking database operations.
"""

import os
import logging
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import NullPool, StaticPool

from app.config import settings

logger = logging.getLogger(__name__)

# Base class for models
Base = declarative_base()

# Global engine and session maker (initialized lazily)
engine = None
async_session_maker = None


def _is_valid_postgres_url(url: str) -> bool:
    """Check if PostgreSQL URL looks valid (not a placeholder)."""
    if not url:
        return False
    if not url.startswith(("postgres://", "postgresql://")):
        return False
    # Check for common placeholder patterns
    placeholders = ["localhost", "your-", "placeholder", "example", "user:password@host"]
    for p in placeholders:
        if p in url.lower():
            return False
    # Must have actual host after @
    if "@" in url:
        after_at = url.split("@")[1] if "@" in url else ""
        if not after_at or after_at.startswith(":") or after_at.startswith("/"):
            return False
    return True


def _get_database_url() -> str:
    """Get and validate database URL, falling back to SQLite if needed."""
    database_url = settings.database_url

    # Check if it's a valid PostgreSQL URL
    if _is_valid_postgres_url(database_url):
        # Railway uses postgres:// but SQLAlchemy needs postgresql://
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
        return database_url

    # Fall back to SQLite
    logger.warning("DATABASE_URL not set or invalid, using SQLite")
    return "sqlite:///./jewelclaw.db"


def _create_engine():
    """Create database engine based on URL."""
    global engine, async_session_maker

    database_url = _get_database_url()

    if database_url.startswith("sqlite"):
        # SQLite for local development/testing
        if ":///" not in database_url:
            database_url = database_url.replace("sqlite://", "sqlite:///")
        database_url = database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
        engine = create_async_engine(
            database_url,
            echo=settings.debug,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        logger.info("Using SQLite database (development mode)")
    elif database_url.startswith("postgresql"):
        # PostgreSQL for production
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        engine = create_async_engine(
            database_url,
            echo=settings.debug,
            pool_size=5,
            max_overflow=10,
            pool_timeout=30,
            pool_recycle=300,  # Recycle connections every 5 min (Railway proxy resets)
            pool_pre_ping=True,
        )
        logger.info("Using PostgreSQL database (production mode)")
    else:
        # Final fallback to SQLite
        logger.warning(f"Unknown database URL format, falling back to SQLite")
        database_url = "sqlite+aiosqlite:///./jewelclaw.db"
        engine = create_async_engine(
            database_url,
            echo=settings.debug,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

    # Create session factory
    async_session_maker = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


# Initialize engine on module load
_create_engine()


async def init_db():
    """Initialize database tables. Won't crash if database unavailable."""
    global engine
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables initialized")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        logger.warning("App will continue but database features may not work")


async def reset_db():
    """Drop all tables and recreate them. USE WITH CAUTION."""
    global engine
    from sqlalchemy import text

    try:
        async with engine.begin() as conn:
            # Drop tables using raw SQL with CASCADE for PostgreSQL
            await conn.execute(text("DROP TABLE IF EXISTS conversations CASCADE"))
            await conn.execute(text("DROP TABLE IF EXISTS metal_rates CASCADE"))
            await conn.execute(text("DROP TABLE IF EXISTS users CASCADE"))
            # Drop enum type if exists (PostgreSQL specific)
            await conn.execute(text("DROP TYPE IF EXISTS languagepreference CASCADE"))
            logger.info("All tables dropped")

            # Recreate tables
            await conn.run_sync(Base.metadata.create_all)
            logger.info("All tables recreated")
        return True
    except Exception as e:
        logger.error(f"Database reset failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


async def close_db():
    """Close database connections."""
    global engine
    if engine:
        try:
            await engine.dispose()
            logger.info("Database connections closed")
        except Exception as e:
            logger.error(f"Error closing database: {e}")


@asynccontextmanager
async def get_db_session():
    """Get database session context manager."""
    global async_session_maker
    if not async_session_maker:
        raise RuntimeError("Database not initialized")

    session = async_session_maker()
    try:
        yield session
        await session.commit()
    except Exception as e:
        await session.rollback()
        logger.error(f"Database error: {e}")
        raise
    finally:
        await session.close()


async def get_db():
    """FastAPI dependency for database sessions."""
    async with get_db_session() as session:
        yield session
