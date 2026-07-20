import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.config import settings
from app.database.database import AsyncSessionLocal, engine
from app.models import models
from app.routers import admin, auth, core, extra, notifications, plans, pesaflux

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


async def seed_data() -> None:
    """Create only missing baseline records after Alembic migrations are applied."""
    async with AsyncSessionLocal() as db:
        try:
            default_plans = [
                {"name": "Intern", "price": 0.0, "daily_tasks_limit": 2, "validity_days": 3, "description": "Free Trial", "is_upgrade_only": False, "is_active": True},
                {"name": "LV1", "price": 20.0, "daily_tasks_limit": 2, "validity_days": 60, "description": "Level 1 Plan", "is_upgrade_only": False, "is_active": True},
                {"name": "LV2", "price": 50.0, "daily_tasks_limit": 5, "validity_days": 60, "description": "Level 2 Plan", "is_upgrade_only": False, "is_active": True},
                {"name": "LV3", "price": 100.0, "daily_tasks_limit": 7, "validity_days": 60, "description": "Level 3 Plan", "is_upgrade_only": False, "is_active": True},
                {"name": "LV4", "price": 150.0, "daily_tasks_limit": 10, "validity_days": 60, "description": "Level 4 Plan", "is_upgrade_only": False, "is_active": True},
                {"name": "LV5", "price": 200.0, "daily_tasks_limit": 15, "validity_days": 60, "description": "Level 5 Plan", "is_upgrade_only": False, "is_active": True},
            ]
            for plan_data in default_plans:
                existing = await db.execute(select(models.Plan).filter(models.Plan.name == plan_data["name"]))
                if existing.scalar_one_or_none() is None:
                    db.add(models.Plan(**plan_data))

            certification = await db.execute(
                select(models.Certification).filter(models.Certification.name == "Video Reviewing Mastery")
            )
            if certification.scalar_one_or_none() is None:
                db.add(
                    models.Certification(
                        name="Video Reviewing Mastery",
                        description="Master the essentials of video assessment...",
                        estimated_time="15 mins",
                        video_url="https://res.cloudinary.com/demo/video/upload/dog.mp4",
                        steps_count=1,
                        is_active=True,
                    )
                )
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("Baseline data seeding failed; run Alembic migrations before starting the API")
            raise


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Schema changes are intentionally applied only via Alembic. This prevents
    # the application process from silently altering production data on startup.
    settings.validate_runtime_security()
    await seed_data()
    yield
    await engine.dispose()


app = FastAPI(title=settings.PROJECT_NAME, version=settings.PROJECT_VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(core.router)
app.include_router(extra.router)
app.include_router(admin.router)
app.include_router(plans.router)
app.include_router(notifications.router)
app.include_router(pesaflux.router)


@app.get("/")
def root() -> dict:
    return {"message": "Atlas API is running"}
