import asyncio
from sqlalchemy import text
from app.database.database import engine

async def audit_db():
    async with engine.begin() as conn:
        print("🔍 Auditing database schema...")
        
        # Table: users
        users_columns = [
            ("deposit_wallet_balance", "FLOAT DEFAULT 0.0"),
            ("withdrawal_wallet_balance", "FLOAT DEFAULT 0.0"),
            ("referral_code", "VARCHAR"),
            ("current_plan_id", "INTEGER"),
            ("plan_start_date", "TIMESTAMP WITH TIME ZONE"),
            ("plan_expiry_date", "TIMESTAMP WITH TIME ZONE"),
            ("plan_purchase_price", "FLOAT DEFAULT 0.0"),
            ("first_name", "VARCHAR"),
            ("last_name", "VARCHAR"),
            ("is_admin", "BOOLEAN DEFAULT FALSE")
        ]
        for col, dtype in users_columns:
            try:
                await conn.execute(text(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} {dtype}"))
                print(f"✅ users.{col} verified/added.")
            except Exception as e:
                print(f"⚠️ Error on users.{col}: {e}")

        # Table: plans
        plans_columns = [
            ("is_upgrade_only", "BOOLEAN DEFAULT FALSE"),
            ("is_active", "BOOLEAN DEFAULT TRUE"),
            ("daily_tasks_limit", "INTEGER DEFAULT 0"),
            ("validity_days", "INTEGER DEFAULT 0"),
            ("price", "FLOAT DEFAULT 0.0"),
            ("description", "TEXT")
        ]
        for col, dtype in plans_columns:
            try:
                await conn.execute(text(f"ALTER TABLE plans ADD COLUMN IF NOT EXISTS {col} {dtype}"))
                print(f"✅ plans.{col} verified/added.")
            except Exception as e:
                print(f"⚠️ Error on plans.{col}: {e}")

        # Table: video_tasks
        tasks_columns = [
            ("reward_amount", "FLOAT DEFAULT 0.0"),
            ("video_url", "VARCHAR"),
            ("title", "VARCHAR"),
            ("description", "TEXT")
        ]
        for col, dtype in tasks_columns:
            try:
                await conn.execute(text(f"ALTER TABLE video_tasks ADD COLUMN IF NOT EXISTS {col} {dtype}"))
                print(f"✅ video_tasks.{col} verified/added.")
            except Exception as e:
                print(f"⚠️ Error on video_tasks.{col}: {e}")

        # Table: certifications
        cert_columns = [
            ("video_url", "VARCHAR"),
            ("estimated_time", "VARCHAR"),
            ("steps_count", "INTEGER DEFAULT 1")
        ]
        for col, dtype in cert_columns:
            try:
                await conn.execute(text(f"ALTER TABLE certifications ADD COLUMN IF NOT EXISTS {col} {dtype}"))
                print(f"✅ certifications.{col} verified/added.")
            except Exception as e:
                print(f"⚠️ Error on certifications.{col}: {e}")

        # Table: referral_codes (check if exists, if not models will create it on startup usually, but let's be safe)
        try:
            await conn.execute(text("ALTER TABLE referral_codes ADD COLUMN IF NOT EXISTS task_rebate_amount FLOAT DEFAULT 0.0"))
            await conn.execute(text("ALTER TABLE referral_codes ADD COLUMN IF NOT EXISTS earned_amount FLOAT DEFAULT 0.0"))
            print("✅ referral_codes columns verified.")
        except Exception as e:
            print(f"⚠️ Error on referral_codes: {e}")

        print("🏁 Database audit complete!")

if __name__ == "__main__":
    asyncio.run(audit_db())
