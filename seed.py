import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.database import AsyncSessionLocal, engine
from app.models import models

async def seed_data():
    async with AsyncSessionLocal() as db:
        # 0. Add Plans
        plans = [
            models.Plan(name="Intern", price=0.0, daily_tasks_limit=2, validity_days=3, description="Free Trial", is_upgrade_only=False),
            models.Plan(name="LV1", price=20.0, daily_tasks_limit=2, validity_days=60, description="Level 1 Plan", is_upgrade_only=False),
            models.Plan(name="LV2", price=50.0, daily_tasks_limit=5, validity_days=60, description="Level 2 Plan", is_upgrade_only=False),
            models.Plan(name="LV3", price=100.0, daily_tasks_limit=7, validity_days=60, description="Level 3 Plan", is_upgrade_only=False),
            models.Plan(name="LV4", price=150.0, daily_tasks_limit=10, validity_days=60, description="Level 4 Plan", is_upgrade_only=False),
        ]
        db.add_all(plans)

        # 1. Add Certifications
        certs = [
            models.Certification(name="Standard Label Training", description="Previously atomic action labels", estimated_time="~25 min", steps_count=3),
            models.Certification(name="Easy Mode Training", description="Simplified coarse labeling", is_active=False),
            models.Certification(name="Auditor Certification", description="Review and audit labeled content", is_active=False),
            models.Certification(name="Labeller (Legacy)", description="Previous labeling certification", is_active=False),
        ]
        db.add_all(certs)
        
        # 2. Add Tasks
        tasks = [
            models.Task(name="Atomic Action Labels", description="Complete training to access labeling tasks", status="locked")
        ]
        db.add_all(tasks)
        
        await db.commit()
        print("✅ Database seeded successfully!")

if __name__ == "__main__":
    asyncio.run(seed_data())
