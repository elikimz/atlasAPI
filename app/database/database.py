import ssl
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings


# Load environment variables
DATABASE_URL = settings.DATABASE_URL

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set. Please configure it in your environment variables.")


def normalize_database_url(database_url: str) -> str:
    """Ensure PostgreSQL URLs use the asyncpg driver required by AsyncEngine."""
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif database_url.startswith("postgresql://") and "+asyncpg" not in database_url:
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return database_url


DATABASE_URL = normalize_database_url(DATABASE_URL)

# SSL context for hosted PostgreSQL providers that require SSL connections.
# For asyncpg, we can use "ssl": "require" or "ssl": True if standard context fails
connect_args = {}
if "neon.tech" in DATABASE_URL or "sslmode" in DATABASE_URL:
    connect_args["ssl"] = "require"
else:
    ssl_context = ssl.create_default_context()
    connect_args["ssl"] = ssl_context

# Async engine configuration:
# - pool_pre_ping checks a pooled connection before handing it to a request.
# - pool_recycle prevents very old idle connections from being reused.
# Together, these avoid asyncpg "connection is closed" errors after DB/proxy idle timeouts.
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_recycle=300,
    pool_size=5,
    max_overflow=10,
    connect_args=connect_args,
)

# SessionMaker
AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# Declarative Base
Base = declarative_base()


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """Provide a short-lived AsyncSession per request and always clean it up."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


__all__ = ["engine", "AsyncSessionLocal", "Base", "get_async_db"]
