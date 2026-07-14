import logging
from logging.handlers import RotatingFileHandler
from fastapi import FastAPI
from sqlalchemy import select, text
from app.routers import auth, core, extra, admin, plans, notifications
from app.routers import pesaflux  # NEW: PesaFlux M-Pesa STK Push (additive)
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.database.database import engine, Base, AsyncSessionLocal
from app.models import models

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", handlers=[
    RotatingFileHandler("uvicorn.log", maxBytes=10*1024*1024, backupCount=5),
    logging.StreamHandler()
])
logger = logging.getLogger("uvicorn")

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
app.include_router(notifications.router)
app.include_router(pesaflux.router)  # NEW: PesaFlux M-Pesa STK Push (additive)

async def run_migrations():
    """Run lightweight migrations to ensure columns exist."""
    async with engine.begin() as conn:
        try:
            # Users table migrations
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS deposit_wallet_balance FLOAT DEFAULT 0.0"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS withdrawal_wallet_balance FLOAT DEFAULT 0.0"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS performance_bonus_balance FLOAT DEFAULT 0.0"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_code VARCHAR"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS current_plan_id INTEGER"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS plan_start_date TIMESTAMP WITH TIME ZONE"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS plan_expiry_date TIMESTAMP WITH TIME ZONE"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS plan_purchase_price FLOAT DEFAULT 0.0"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_trained BOOLEAN DEFAULT FALSE"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR DEFAULT 'user'"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS withdrawal_password VARCHAR"))
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_suspended BOOLEAN DEFAULT FALSE"))
            
            # Backfill admin role
            await conn.execute(text("UPDATE users SET role = 'admin' WHERE is_admin = TRUE"))
            
            # Video tasks table migrations
            await conn.execute(text("ALTER TABLE video_tasks ADD COLUMN IF NOT EXISTS reward_amount FLOAT DEFAULT 0.0"))
            await conn.execute(text("ALTER TABLE video_tasks ADD COLUMN IF NOT EXISTS video_url VARCHAR"))
            await conn.execute(text("ALTER TABLE video_tasks ADD COLUMN IF NOT EXISTS plan_id INTEGER REFERENCES plans(id) ON DELETE CASCADE"))
            
            # Certifications table migrations
            await conn.execute(text("ALTER TABLE certifications ADD COLUMN IF NOT EXISTS video_url VARCHAR"))
            
            # Plans table migrations
            await conn.execute(text("ALTER TABLE plans ADD COLUMN IF NOT EXISTS is_upgrade_only BOOLEAN DEFAULT FALSE"))
            await conn.execute(text("ALTER TABLE plans ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE"))
            
            # OTP table migrations
            await conn.execute(text("ALTER TABLE otps ADD COLUMN IF NOT EXISTS is_used BOOLEAN DEFAULT FALSE"))
            await conn.execute(text("ALTER TABLE otps ADD COLUMN IF NOT EXISTS ip_address VARCHAR"))
            await conn.execute(text("ALTER TABLE certifications ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE"))
            
            # ── PesaFlux payments table (NEW — additive only) ──────────────────
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS pesaflux_payments (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    plan_id INTEGER REFERENCES plans(id) ON DELETE SET NULL,
                    reference VARCHAR NOT NULL UNIQUE,
                    transaction_request_id VARCHAR,
                    provider_transaction_id VARCHAR,
                    mpesa_receipt VARCHAR,
                    phone VARCHAR NOT NULL,
                    amount FLOAT NOT NULL,
                    amount_usd FLOAT NOT NULL DEFAULT 0.0,
                    status VARCHAR NOT NULL DEFAULT 'pending',
                    provider VARCHAR NOT NULL DEFAULT 'pesaflux',
                    plan_activated VARCHAR NOT NULL DEFAULT 'no',
                    payment_type VARCHAR NOT NULL DEFAULT 'purchase',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    updated_at TIMESTAMP WITH TIME ZONE,
                    completed_at TIMESTAMP WITH TIME ZONE
                )
            """))
            # Ensure indexes exist for pesaflux_payments
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_pesaflux_payments_reference ON pesaflux_payments(reference)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_pesaflux_payments_user_id ON pesaflux_payments(user_id)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_pesaflux_payments_status ON pesaflux_payments(status)"))
            # Backfill amount_usd column if table existed before this migration
            await conn.execute(text("ALTER TABLE pesaflux_payments ADD COLUMN IF NOT EXISTS amount_usd FLOAT NOT NULL DEFAULT 0.0"))
            await conn.execute(text("ALTER TABLE pesaflux_payments ADD COLUMN IF NOT EXISTS plan_activated VARCHAR NOT NULL DEFAULT 'no'"))
            await conn.execute(text("ALTER TABLE pesaflux_payments ADD COLUMN IF NOT EXISTS payment_type VARCHAR NOT NULL DEFAULT 'purchase'"))

            # Payments table migrations
            await conn.execute(text("ALTER TABLE payments ADD COLUMN IF NOT EXISTS type VARCHAR DEFAULT 'payout'"))
            await conn.execute(text("ALTER TABLE payments ADD COLUMN IF NOT EXISTS payment_method VARCHAR"))
            await conn.execute(text("ALTER TABLE payments ADD COLUMN IF NOT EXISTS network VARCHAR"))
            await conn.execute(text("ALTER TABLE payments ADD COLUMN IF NOT EXISTS proof_url VARCHAR"))
            await conn.execute(text("ALTER TABLE payments ADD COLUMN IF NOT EXISTS admin_notes VARCHAR"))

            # Users: first-purchase flag
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS has_purchased_first_package BOOLEAN DEFAULT FALSE"))

            # Upgrade Refunds table (3-day lock mechanism)
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS upgrade_refunds (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    amount FLOAT NOT NULL,
                    status VARCHAR DEFAULT 'pending',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    release_at TIMESTAMP WITH TIME ZONE NOT NULL,
                    released_at TIMESTAMP WITH TIME ZONE,
                    plan_history_id INTEGER REFERENCES user_plan_history(id) ON DELETE SET NULL
                )
            """))

            # Earnings Log table (for GMT-based period calculations)
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS earnings_logs (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                    amount FLOAT NOT NULL,
                    type VARCHAR NOT NULL,
                    description VARCHAR,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """))
            
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
                {"name": "LV4", "price": 150.0, "daily_tasks_limit": 10, "validity_days": 60, "description": "Level 4 Plan", "is_upgrade_only": False, "is_active": True},
                {"name": "LV5", "price": 200.0, "daily_tasks_limit": 15, "validity_days": 60, "description": "Level 5 Plan", "is_upgrade_only": False, "is_active": True}
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
            existing_cert = result.scalar_one_or_none()
            if not existing_cert:
                default_cert = models.Certification(
                    name="Video Reviewing Mastery",
                    description="Master the essentials of video assessment...",
                    estimated_time="15 mins",
                    video_url="https://res.cloudinary.com/demo/video/upload/dog.mp4",
                    steps_count=1,
                    is_active=True
                )
                db.add(default_cert)
                await db.commit()
                print("✅ Default certification seeded.")
            elif not getattr(existing_cert, "is_active", True):
                existing_cert.is_active = True
                await db.commit()
                print("✅ Default certification reactivated.")
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
