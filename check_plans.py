import asyncio
from app.database.database import AsyncSessionLocal
from sqlalchemy import text

async def check():
    async with AsyncSessionLocal() as db:
        result = await db.execute(text("SELECT id, name, daily_tasks_limit FROM plans"))
        plans = result.all()
        for plan in plans:
            print(f"ID: {plan.id}, Name: {plan.name}, Limit: {plan.daily_tasks_limit}")

if __name__ == "__main__":
    asyncio.run(check())
