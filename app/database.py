"""
Database connection and session management.
Supports PostgreSQL (production) and SQLite (development/testing).
Uses SQLAlchemy async for non-blocking database operations.
"""

import logging
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import NullPool, StaticPool

from app.config import settings

logger = logging.getLogger(__name__)

# Configure database URL for async support
database_url = settings.database_url

# Railway uses postgres:// but SQLAlchemy needs postgresql://
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

# Determine database type and configure appropriately
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
        poolclass=NullPool,  # Better for serverless/Railway
        pool_pre_ping=True,  # Verify connections are alive
    )
    logger.info("Using PostgreSQL database (production mode)")
else:
    raise ValueError(f"Unsupported database URL format: {database_url}")

# Session factory
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Base class for models
Base = declarative_base()


async def init_db():
    """Initialize database tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables initialized")


async def close_db():
    """Close database connections."""
    await engine.dispose()
    logger.info("Database connections closed")


@asynccontextmanager
async def get_db_session():
    """Get database session context manager."""
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
