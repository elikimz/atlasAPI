from fastapi import FastAPI
from sqlalchemy import select, text
from app.routers import auth, core, extra, admin, plans
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.database.database import engine, Base, AsyncSessionLocal
from app.models import models

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

async def run_migrations():
    """Run lightweight migrations to ensure columns exist."""
    async with engine.begin() as conn:
        try:
            # Users table migrations
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS deposit_wallet_balance FLOAT DEFAULT 0.0"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS withdrawal_wallet_balance FLOAT DEFAULT 0.0"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_code VARCHAR"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS current_plan_id INTEGER"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS plan_start_date TIMESTAMP WITH TIME ZONE"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS plan_expiry_date TIMESTAMP WITH TIME ZONE"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS plan_purchase_price FLOAT DEFAULT 0.0"))
            
            # Video tasks table migrations
            await conn.execute(text("ALTER TABLE video_tasks ADD COLUMN IF NOT EXISTS reward_amount FLOAT DEFAULT 0.0"))
            await conn.execute(text("ALTER TABLE video_tasks ADD COLUMN IF NOT EXISTS video_url VARCHAR"))
            
            # Certifications table migrations
            await conn.execute(text("ALTER TABLE certifications ADD COLUMN IF NOT EXISTS video_url VARCHAR"))
            
            # Plans table migrations
            await conn.execute(text("ALTER TABLE plans ADD COLUMN IF NOT EXISTS is_upgrade_only BOOLEAN DEFAULT FALSE"))
            await conn.execute(text("ALTER TABLE plans ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE"))
            
            # OTP table migrations
            await conn.execute(text("ALTER TABLE otps ADD COLUMN IF NOT EXISTS is_used BOOLEAN DEFAULT FALSE"))
            await conn.execute(text("ALTER TABLE otps ADD COLUMN IF NOT EXISTS ip_address VARCHAR"))
            
            print("✅ Startup migrations completed successfully.")
        except Exception as e:
            print(f"⚠️ Migration Notice (Safe to ignore if columns exist): {e}")

async def seed_data():
    """Seed initial data if tables are empty."""
    async with AsyncSessionLocal() as db:
        try:
            # Seed or normalize default plans. This intentionally updates existing
            # rows too, so older databases keep Intern as a zero-cost free trial.
            default_plans_data = [
                {"name": "Intern", "price": 0.0, "daily_tasks_limit": 2, "validity_days": 3, "description": "Free Trial", "is_upgrade_only": False, "is_active": True},
                {"name": "LV1", "price": 20.0, "daily_tasks_limit": 2, "validity_days": 60, "description": "Level 1 Plan", "is_upgrade_only": False, "is_active": True},
                {"name": "LV2", "price": 50.0, "daily_tasks_limit": 5, "validity_days": 60, "description": "Level 2 Plan", "is_upgrade_only": False, "is_active": True},
                {"name": "LV3", "price": 100.0, "daily_tasks_limit": 7, "validity_days": 60, "description": "Level 3 Plan", "is_upgrade_only": False, "is_active": True},
                {"name": "LV4", "price": 150.0, "daily_tasks_limit": 10, "validity_days": 60, "description": "Level 4 Plan", "is_upgrade_only": False, "is_active": True}
            ]
            changed_plans = False
            for plan_data in default_plans_data:
                result = await db.execute(select(models.Plan).filter(models.Plan.name == plan_data["name"]))
                existing_plan = result.scalar_one_or_none()
                if existing_plan:
                    for field, value in plan_data.items():
                        if getattr(existing_plan, field) != value:
                            setattr(existing_plan, field, value)
                            changed_plans = True
                else:
                    db.add(models.Plan(**plan_data))
                    changed_plans = True
            if changed_plans:
                await db.commit()
                print("✅ Default plans seeded/updated.")

            # Seed default certification
            result = await db.execute(select(models.Certification).filter(models.Certification.name == "Video Reviewing Mastery"))
            if not result.scalars().first():
                default_cert = models.Certification(
                    name="Video Reviewing Mastery",
                    description="Master the essentials of video assessment...",
                    estimated_time="15 mins",
                    video_url="https://res.cloudinary.com/demo/video/upload/dog.mp4",
                    steps_count=1
                )
                db.add(default_cert)
                await db.commit()
                print("✅ Default certification seeded.")
        except Exception as e:
            print(f"❌ Seeding error: {e}")
            await db.rollback()

@app.on_event("startup")
async def on_startup():
    # 1. Create tables if they don't exist
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # 2. Run migrations
    await run_migrations()
    
    # 3. Seed initial data
    await seed_data()

@app.on_event("shutdown")
async def on_shutdown():
    await engine.dispose()

@app.get("/")
def root():
    return {"message": "Adpulse API is running 🚀"}
