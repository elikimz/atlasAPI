import asyncio
from sqlalchemy import text
from app.database.database import engine

async def fix_plans_table():
    async with engine.begin() as conn:
        try:
            # Check and add missing columns to 'plans' table
            await conn.execute(text("ALTER TABLE plans ADD COLUMN IF NOT EXISTS is_upgrade_only BOOLEAN DEFAULT FALSE"))
            await conn.execute(text("ALTER TABLE plans ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE"))
            print("✅ 'plans' table columns checked and updated successfully!")
        except Exception as e:
            print(f"❌ Error updating 'plans' table: {e}")

if __name__ == "__main__":
    asyncio.run(fix_plans_table())
