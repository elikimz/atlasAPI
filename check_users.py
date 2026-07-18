import asyncio
from sqlalchemy import select
from app.database.database import engine
from app.models import models

async def check():
    async with engine.connect() as conn:
        result = await conn.execute(select(models.User))
        users = result.fetchall()
        print(f"Total users found: {len(users)}")
        for user in users:
            print(f"ID: {user.id}, Username: {user.username}, Email: {user.email}")
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(check())
