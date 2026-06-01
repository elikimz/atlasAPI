import asyncio
from sqlalchemy import text
from app.database.database import AsyncSessionLocal

async def migrate_otp():
    async with AsyncSessionLocal() as db:
        print("--- Migrating OTP Table ---")
        try:
            # Add is_used column
            await db.execute(text("ALTER TABLE otps ADD COLUMN IF NOT EXISTS is_used BOOLEAN DEFAULT FALSE"))
            # Add ip_address column
            await db.execute(text("ALTER TABLE otps ADD COLUMN IF NOT EXISTS ip_address VARCHAR"))
            await db.commit()
            print("✅ OTP table migrated successfully.")
        except Exception as e:
            print(f"❌ Migration failed: {e}")
            await db.rollback()

if __name__ == "__main__":
    asyncio.run(migrate_otp())
