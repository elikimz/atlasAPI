import asyncio
from sqlalchemy import text
from app.database.database import engine

async def apply_migration():
    async with engine.begin() as conn:
        try:
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS plan_purchase_price FLOAT DEFAULT 0.0"))
            print("✅ Column 'plan_purchase_price' added successfully to 'users' table!")
        except Exception as e:
            print(f"❌ Error adding column: {e}")

if __name__ == "__main__":
    asyncio.run(apply_migration())
