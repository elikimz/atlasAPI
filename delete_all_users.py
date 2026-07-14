import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.database import engine, Base, AsyncSessionLocal
from app.models import models

async def delete_all_users_and_verify():
    async with AsyncSessionLocal() as db:
        try:
            # Delete all users, which should cascade to related tables
            # due to 'cascade="all, delete-orphan"' in relationships.
            await db.execute(models.User.__table__.delete())
            await db.commit()
            print("✅ All users and their related data deleted successfully.")

            # Verify deletion
            result = await db.execute(select(models.User))
            remaining_users = result.scalars().all()
            if not remaining_users:
                print("✅ Verification successful: Users table is empty.")
            else:
                print(f"❌ Verification failed: {len(remaining_users)} users still exist.")

        except Exception as e:
            await db.rollback()
            print(f"❌ Error during user deletion or verification: {e}")

async def main():
    await delete_all_users_and_verify()

if __name__ == "__main__":
    asyncio.run(main())
