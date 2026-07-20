"""Seed a deterministic, non-production dataset for local audit and regression testing.

Usage:
    ATLAS_ALLOW_DEMO_SEED=1 DATABASE_URL=sqlite+aiosqlite:///./atlas-demo.db \
      python scripts/seed_audit_data.py

The script is intentionally blocked unless ATLAS_ALLOW_DEMO_SEED=1 is set. It is
idempotent and should be run only after Alembic migrations have been applied.
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Type

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.database.database import AsyncSessionLocal
from app.models import models
from app.routers.auth import get_password_hash


DEMO_PASSWORD = os.getenv("ATLAS_DEMO_PASSWORD", "AtlasDemo!2026")
NOW = datetime.now(timezone.utc)


async def find_one(session: AsyncSession, model: Type[Any], **criteria: Any) -> Any | None:
    conditions = [getattr(model, key) == value for key, value in criteria.items()]
    return await session.scalar(select(model).where(*conditions))


async def get_or_create(session: AsyncSession, model: Type[Any], lookup: dict[str, Any], defaults: dict[str, Any]) -> Any:
    instance = await find_one(session, model, **lookup)
    if instance is None:
        instance = model(**lookup, **defaults)
        session.add(instance)
        await session.flush()
        return instance

    for key, value in defaults.items():
        setattr(instance, key, value)
    await session.flush()
    return instance


async def seed() -> None:
    if os.getenv("ATLAS_ALLOW_DEMO_SEED") != "1":
        raise SystemExit("Refusing to seed data. Set ATLAS_ALLOW_DEMO_SEED=1 for a local non-production database.")

    async with AsyncSessionLocal() as session:
        # Product catalogue and training fixtures.
        starter = await get_or_create(
            session,
            models.Plan,
            {"name": "Audit Starter"},
            {
                "price": 25.0,
                "daily_tasks_limit": 3,
                "validity_days": 30,
                "description": "Entry plan used by local audit scenarios.",
                "is_active": True,
                "is_upgrade_only": False,
            },
        )
        growth = await get_or_create(
            session,
            models.Plan,
            {"name": "Audit Growth"},
            {
                "price": 75.0,
                "daily_tasks_limit": 8,
                "validity_days": 30,
                "description": "Upgrade plan used by local audit scenarios.",
                "is_active": True,
                "is_upgrade_only": True,
            },
        )
        certification = await get_or_create(
            session,
            models.Certification,
            {"name": "Audit Video Review Basics"},
            {
                "description": "Representative training certification for local verification.",
                "estimated_time": "15 minutes",
                "video_url": "https://example.test/training/audit-video-review",
                "steps_count": 3,
                "is_active": True,
            },
        )
        await get_or_create(
            session,
            models.Task,
            {"name": "Audit: Brand-safety video review"},
            {
                "description": "Review a representative video and classify it against the supplied guidelines.",
                "required_certification_id": certification.id,
                "status": "available",
            },
        )
        await get_or_create(
            session,
            models.VideoTask,
            {"title": "Audit: Sample rewarded review"},
            {
                "plan_id": starter.id,
                "description": "A safe local task used to exercise task-state and reward paths.",
                "video_url": "https://example.test/tasks/audit-sample",
                "reward_amount": 2.5,
            },
        )

        # Accounts have deterministic, test-only credentials. Use a non-production database.
        admin = await get_or_create(
            session,
            models.User,
            {"username": "audit_admin"},
            {
                "first_name": "Audit",
                "last_name": "Administrator",
                "email": "audit_admin@example.test",
                "phone_number": "+254700000100",
                "password_hash": get_password_hash(DEMO_PASSWORD),
                "role": "admin",
                "is_admin": True,
                "is_trained": True,
                "deposit_wallet_balance": 0.0,
                "withdrawal_wallet_balance": 0.0,
                "performance_bonus_balance": 0.0,
                "referral_code": "AUDITADMIN",
                "is_suspended": False,
            },
        )
        trained = await get_or_create(
            session,
            models.User,
            {"username": "audit_trained"},
            {
                "first_name": "Taylor",
                "last_name": "Trained",
                "email": "audit_trained@example.test",
                "phone_number": "+254700000101",
                "password_hash": get_password_hash(DEMO_PASSWORD),
                "role": "user",
                "is_admin": False,
                "is_trained": True,
                "deposit_wallet_balance": 140.0,
                "withdrawal_wallet_balance": 32.5,
                "performance_bonus_balance": 7.5,
                "referral_code": "AUDITTRAINED",
                "current_plan_id": starter.id,
                "plan_start_date": NOW - timedelta(days=5),
                "plan_expiry_date": NOW + timedelta(days=25),
                "plan_purchase_price": starter.price,
                "has_purchased_first_package": True,
                "is_suspended": False,
            },
        )
        new_user = await get_or_create(
            session,
            models.User,
            {"username": "audit_new"},
            {
                "first_name": "Nora",
                "last_name": "New",
                "email": "audit_new@example.test",
                "phone_number": "+254700000102",
                "password_hash": get_password_hash(DEMO_PASSWORD),
                "role": "user",
                "is_admin": False,
                "is_trained": False,
                "deposit_wallet_balance": 40.0,
                "withdrawal_wallet_balance": 0.0,
                "performance_bonus_balance": 0.0,
                "referral_code": "AUDITNEW",
                "is_suspended": False,
            },
        )
        await get_or_create(
            session,
            models.User,
            {"username": "audit_suspended"},
            {
                "first_name": "Sam",
                "last_name": "Suspended",
                "email": "audit_suspended@example.test",
                "phone_number": "+254700000103",
                "password_hash": get_password_hash(DEMO_PASSWORD),
                "role": "user",
                "is_admin": False,
                "is_trained": False,
                "deposit_wallet_balance": 0.0,
                "withdrawal_wallet_balance": 0.0,
                "performance_bonus_balance": 0.0,
                "referral_code": "AUDITSUSPENDED",
                "is_suspended": True,
            },
        )

        await get_or_create(
            session,
            models.UserCertification,
            {"user_id": trained.id, "certification_id": certification.id},
            {
                "status": "completed",
                "started_at": NOW - timedelta(days=4),
                "completed_at": NOW - timedelta(days=4, minutes=-15),
            },
        )
        await get_or_create(
            session,
            models.UserPlanHistory,
            {"user_id": trained.id, "plan_id": starter.id, "status": "active"},
            {
                "purchase_price": starter.price,
                "purchased_at": NOW - timedelta(days=5),
                "expires_at": NOW + timedelta(days=25),
                "refunded_amount": 0.0,
            },
        )
        await get_or_create(
            session,
            models.ReferralCode,
            {"code": "AUDITTRAINED"},
            {
                "user_id": trained.id,
                "signups_count": 1,
                "trained_count": 0,
                "tier_a_invite_earnings": 2.5,
                "tier_b_invite_earnings": 0.0,
                "tier_c_invite_earnings": 0.0,
                "tier_a_task_rebate": 1.0,
                "tier_b_task_rebate": 0.0,
                "tier_c_task_rebate": 0.0,
                "earned_amount": 2.5,
                "task_rebate_amount": 1.0,
            },
        )
        await get_or_create(
            session,
            models.ReferralRelationship,
            {"user_id": new_user.id},
            {
                "referrer_id": trained.id,
                "referral_code_used": "AUDITTRAINED",
            },
        )

        # Payment history and notification states provide realistic reporting fixtures.
        await get_or_create(
            session,
            models.Payment,
            {"user_id": trained.id, "period": "audit-deposit-001", "type": "deposit"},
            {
                "amount": 100.0,
                "status": "paid",
                "payment_method": "M-Pesa",
                "network": "Safaricom",
                "admin_notes": "Representative settled deposit.",
            },
        )
        await get_or_create(
            session,
            models.Payment,
            {"user_id": trained.id, "period": "audit-payout-001", "type": "payout"},
            {
                "amount": 32.5,
                "status": "pending",
                "payment_method": "M-Pesa",
                "network": "Safaricom",
                "admin_notes": "Representative payout awaiting review.",
            },
        )
        await get_or_create(
            session,
            models.Notification,
            {"user_id": trained.id, "title": "Audit dataset ready"},
            {
                "message": "Your representative local audit data has been prepared.",
                "type": "success",
                "is_read": False,
            },
        )
        await get_or_create(
            session,
            models.Notification,
            {"user_id": None, "title": "Local audit notice"},
            {
                "message": "This is deterministic test data only; do not use it in production.",
                "type": "info",
                "is_read": False,
            },
        )

        # Keep the second plan referenced so regression fixtures can exercise upgrades.
        assert growth.is_active
        await session.commit()

    print("Seeded representative audit data successfully.")
    print("Test accounts: audit_admin, audit_trained, audit_new, audit_suspended")
    print("Password for local test accounts is set by ATLAS_DEMO_PASSWORD (default: AtlasDemo!2026).")


if __name__ == "__main__":
    asyncio.run(seed())
