import asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.database import AsyncSessionLocal, engine
from app.models import models

async def seed_data():
    async with AsyncSessionLocal() as db:
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
