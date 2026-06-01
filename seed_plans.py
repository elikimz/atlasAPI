import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.database import AsyncSessionLocal, engine
from app.models import models
from sqlalchemy.future import select

async def seed_dummy_plans():
    async with AsyncSessionLocal() as db:
        # Check if plans already exist to avoid duplicates
        result = await db.execute(select(models.Plan))
        existing_plans = result.scalars().all()
        
        if not existing_plans:
            dummy_plans = [
                models.Plan(name="Intern", price=0.0, daily_tasks_limit=2, validity_days=3, description="Free Trial", is_upgrade_only=False),
                models.Plan(name="LV1", price=20.0, daily_tasks_limit=2, validity_days=60, description="Level 1 Plan", is_upgrade_only=False),
                models.Plan(name="LV2", price=50.0, daily_tasks_limit=5, validity_days=60, description="Level 2 Plan", is_upgrade_only=False),
                models.Plan(name="LV3", price=100.0, daily_tasks_limit=7, validity_days=60, description="Level 3 Plan", is_upgrade_only=False),
                models.Plan(name="LV4", price=150.0, daily_tasks_limit=10, validity_days=60, description="Level 4 Plan", is_upgrade_only=False),
                # Additional dummy plans for more testing
                models.Plan(name="LV5 (Dummy)", price=200.0, daily_tasks_limit=15, validity_days=90, description="Dummy Level 5", is_upgrade_only=False),
                models.Plan(name="Pro (Dummy)", price=500.0, daily_tasks_limit=20, validity_days=365, description="Dummy Pro Plan", is_upgrade_only=False),
            ]
            db.add_all(dummy_plans)
            await db.commit()
            print("✅ Dummy plans seeded successfully!")
        else:
            print("ℹ️ Plans already exist in the database. Skipping seeding.")

if __name__ == "__main__":
    asyncio.run(seed_dummy_plans())
