from fastapi import FastAPI
from app.routers import auth, core, extra, admin
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings



app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(core.router)
app.include_router(extra.router)
app.include_router(admin.router)


@app.on_event("startup")
async def on_startup():
    """Ensure all tables and columns are created on startup."""
    from app.database.database import engine, Base
    from app.models import models
    from sqlalchemy import text
    
    async with engine.begin() as conn:
        # 1. Create any missing tables
        await conn.run_sync(Base.metadata.create_all)
        
        # 2. Add missing columns to existing tables (Alembic-style safety)
        try:
            # Check for wallet columns in users table
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS deposit_wallet_balance FLOAT DEFAULT 0.0"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS withdrawal_wallet_balance FLOAT DEFAULT 0.0"))
            # Check for referral_code column in users table
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_code VARCHAR"))
            # Check for reward_amount in video_tasks
            await conn.execute(text("ALTER TABLE video_tasks ADD COLUMN IF NOT EXISTS reward_amount FLOAT DEFAULT 0.0"))
            # Check for video_url in video_tasks
            await conn.execute(text("ALTER TABLE video_tasks ADD COLUMN IF NOT EXISTS video_url VARCHAR"))
        except Exception as e:
            print(f"Migration Notice (Safe to ignore if columns exist): {e}")

@app.get("/")
def root():
    return {"message": "Adpulse API is running 🚀"}
    