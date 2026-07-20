import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, inspect as sa_inspect, text

from app.config import settings
from app.database.database import AsyncSessionLocal, engine, Base
from app.models import models
from app.routers import admin, auth, core, extra, notifications, plans, pesaflux

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


# Type name mapping for ALTER TABLE DDL
_TYPE_MAP = {
    "VARCHAR": "VARCHAR(255)",
    "String": "VARCHAR(255)",
    "INTEGER": "INTEGER",
    "Integer": "INTEGER",
    "FLOAT": "FLOAT",
    "Float": "FLOAT",
    "BOOLEAN": "BOOLEAN",
    "Boolean": "BOOLEAN",
    "TIMESTAMP": "TIMESTAMP",
    "DateTime": "TIMESTAMP",
    "TEXT": "TEXT",
    "Text": "TEXT",
    "BigInteger": "BIGINT",
}


def _column_ddl_type(column) -> str:
    """Derive a safe DDL type string from a SQLAlchemy Column."""
    type_name = type(column.type).__name__
    if type_name == "String" and column.type.length:
        return f"VARCHAR({column.type.length})"
    return _TYPE_MAP.get(type_name, "TEXT")


def _add_missing_columns_sync(connection) -> None:
    """Synchronous helper: inspect DB and ALTER TABLE for missing columns."""
    inspector = sa_inspect(connection)
    table_names = set(inspector.get_table_names())

    for table in Base.metadata.sorted_tables:
        # Create missing tables
        if table.name not in table_names:
            table.create(connection)
            logger.info("Schema auto-fix: created missing table '%s'", table.name)
            table_names.add(table.name)
            continue

        # Add missing columns
        existing = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing:
                continue
            ddl_type = _column_ddl_type(column)
            nullable_sql = "NOT NULL" if not column.nullable else "NULL"
            sql = f"ALTER TABLE {table.name} ADD COLUMN {column.name} {ddl_type} {nullable_sql}"
            try:
                connection.execute(text(sql))
                logger.info("Schema auto-fix: added '%s.%s' (%s)", table.name, column.name, ddl_type)
            except Exception as exc:
                logger.warning("Schema auto-fix: could not add '%s.%s': %s", table.name, column.name, exc)

    # Backfill full_name for existing withdrawal_accounts rows
    try:
        if "withdrawal_accounts" in table_names:
            wa_cols = {col["name"] for col in inspector.get_columns("withdrawal_accounts")}
            if "full_name" in wa_cols:
                connection.execute(text(
                    "UPDATE withdrawal_accounts SET full_name = address WHERE full_name IS NULL"
                ))
    except Exception:
        pass


async def _ensure_schema() -> None:
    """Ensure all model columns exist in the live database on startup.

    Azure App Service deploys code without running ``alembic upgrade head``.
    This routine patches the gap by adding any columns that the Alembic
    consolidation migration (6ab7d2e8f490) would have added, plus any
    columns added to models after that migration was written.
    """
    try:
        async with engine.begin() as conn:
            await conn.run_sync(_add_missing_columns_sync)
    except Exception:
        logger.exception("Schema auto-fix failed; ensure Alembic migrations are current")


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
    settings.validate_runtime_security()
    await _ensure_schema()
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
