import ssl
from app.config import settings

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, Session, declarative_base

# Load environment variables
DATABASE_URL = settings.DATABASE_URL

if not DATABASE_URL:
    # In production, ensure DATABASE_URL is set in environment variables
    print("⚠️ WARNING: DATABASE_URL not found in environment variables.")

# --- SSL Context (optional for cloud DBs like Supabase or PlanetScale) ---
ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

# --- Async Engine ---
engine = create_async_engine(
    DATABASE_URL,
    echo=True,  # Set to False in production
    connect_args={"ssl": ssl_context}
)

# --- SessionMaker ---
AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # ✅ FIX: prevents attributes from expiring after commit
    autocommit=False,
    autoflush=False,
)

# --- Declarative Base ---
Base = declarative_base()

# --- Dependency for FastAPI routes ---
async def get_async_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()








__all__ = ["engine", "AsyncSessionLocal", "Base", "get_async_db"]