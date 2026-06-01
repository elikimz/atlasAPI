from fastapi import FastAPI
from sqlalchemy import select
from app.routers import auth, core, extra, admin, plans
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
app.include_router(plans.router)


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
            # Check for video_url in certifications
            await conn.execute(text("ALTER TABLE certifications ADD COLUMN IF NOT EXISTS video_url VARCHAR"))
        except Exception as e:
            print(f"Migration Notice (Safe to ignore if columns exist): {e}")

        # Add new columns for plan management
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS current_plan_id INTEGER"))
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS plan_start_date TIMESTAMP WITH TIME ZONE"))
        await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS plan_expiry_date TIMESTAMP WITH TIME ZONE"))

        # Seed default plans if none exist
        from app.database.database import get_async_db
        async for db in get_async_db():
            try:
                # Check if any plans exist
                result = await db.execute(select(models.Plan))
                if not result.scalar_one_or_none():
                    default_plans_data = [
                        {"name": "Intern", "price": 0.0, "daily_tasks_limit": 2, "validity_days": 3, "description": "Free Trial", "is_upgrade_only": False},
                        {"name": "LV1", "price": 20.0, "daily_tasks_limit": 2, "validity_days": 60, "description": "Level 1 Plan", "is_upgrade_only": False},
                        {"name": "LV2", "price": 50.0, "daily_tasks_limit": 5, "validity_days": 60, "description": "Level 2 Plan", "is_upgrade_only": False},
                        {"name": "LV3", "price": 100.0, "daily_tasks_limit": 7, "validity_days": 60, "description": "Level 3 Plan", "is_upgrade_only": False},
                        {"name": "LV4", "price": 150.0, "daily_tasks_limit": 10, "validity_days": 60, "description": "Level 4 Plan", "is_upgrade_only": False}
                    ]
                    for plan_data in default_plans_data:
                        plan = models.Plan(**plan_data)
                        db.add(plan)
                    await db.commit()
            finally:
                await db.close()
                break

    # Seed default training if none exists
    from app.database.database import get_async_db
    async for db in get_async_db():
        try:
            result = await db.execute(select(models.Certification).filter(models.Certification.name == "Video Reviewing Mastery"))
            if not result.scalar_one_or_none():
                default_cert = models.Certification(
                    name="Video Reviewing Mastery",
                    description="Master the essentials of video assessment in this focused, single-video module. Gain the key insight required for standard tasks and become a qualified reviewer. This efficient course prepares you for premium-paying tasks.",
                    estimated_time="15 mins",
                    video_url="https://res.cloudinary.com/demo/video/upload/dog.mp4", # Placeholder Cloudinary URL
                    steps_count=1
                )
                db.add(default_cert)
                await db.commit()
        finally:
            await db.close()
            break

@app.on_event("shutdown")
async def on_shutdown():
    """Dispose pooled DB connections when the application shuts down."""
    from app.database.database import engine

    await engine.dispose()


@app.get("/")
def root():
    return {"message": "Adpulse API is running 🚀"}
    