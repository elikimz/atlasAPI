from datetime import datetime, timedelta, timezone
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import models
from app.routers.extra import ReferralNetworkResponse, _load_referral_network, get_active_referrals


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(models.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.anyio
async def test_referral_network_is_user_scoped_and_counts_each_member_once(db: AsyncSession) -> None:
    now = datetime.now(timezone.utc)
    intern = models.Plan(name="Intern", price=0, membership_level=0, daily_tasks_limit=2, validity_days=3)
    lv1 = models.Plan(name="LV1", price=20, membership_level=1, daily_tasks_limit=2, validity_days=60)
    lv2 = models.Plan(name="LV2", price=50, membership_level=2, daily_tasks_limit=5, validity_days=60)
    lv3 = models.Plan(name="LV3", price=100, membership_level=3, daily_tasks_limit=7, validity_days=60)
    root = models.User(username="root", first_name="Root", last_name="Member", password_hash="x")
    direct = models.User(
        username="direct",
        first_name="Direct",
        last_name="Member",
        password_hash="x",
        current_plan=lv1,
        plan_purchase_price=20,
        has_purchased_first_package=True,
    )
    second = models.User(username="second", first_name="Second", last_name="Member", password_hash="x")
    third = models.User(
        username="third",
        first_name="Third",
        last_name="Member",
        password_hash="x",
        current_plan=lv3,
        plan_purchase_price=100,
        has_purchased_first_package=True,
        is_suspended=True,
    )
    outside = models.User(username="outside", password_hash="x")
    db.add_all([intern, lv1, lv2, lv3, root, direct, second, third, outside])
    await db.flush()

    db.add_all([
        models.ReferralRelationship(user_id=direct.id, referrer_id=root.id),
        models.ReferralRelationship(user_id=second.id, referrer_id=direct.id),
        models.ReferralRelationship(user_id=third.id, referrer_id=second.id),
        models.ReferralRelationship(user_id=outside.id, referrer_id=outside.id),
        models.UserPlanHistory(
            user_id=direct.id,
            plan_id=lv1.id,
            purchase_price=20,
            purchased_at=now - timedelta(days=10),
            expires_at=now + timedelta(days=50),
            status="upgraded",
        ),
        models.UserPlanHistory(
            user_id=direct.id,
            plan_id=lv2.id,
            purchase_price=50,
            purchased_at=now - timedelta(days=2),
            expires_at=now + timedelta(days=58),
            status="active",
        ),
    ])
    await db.flush()

    result = await _load_referral_network(root, db)
    ReferralNetworkResponse.model_validate(result)

    assert result["overview"] == {
        "total": 3,
        "invested": 2,
        "not_invested": 1,
        "active": 2,
        "inactive": 1,
        "tier_a": 1,
        "tier_b": 1,
        "tier_c": 1,
    }
    assert [member["username"] for member in result["tiers"]["A"]["members"]] == ["direct"]
    assert result["tiers"]["A"]["members"][0]["membership_level"] == "Level 1"
    assert result["tiers"]["A"]["members"][0]["has_upgraded"] is True
    assert result["tiers"]["B"]["members"][0]["investment_status"] == "Not invested"
    assert result["tiers"]["B"]["members"][0]["sponsor_username"] == "direct"
    assert result["tiers"]["C"]["members"][0]["account_status"] == "Inactive"
    assert result["tiers"]["C"]["members"][0]["membership_level"] == "Level 3"
    assert result["tiers"]["A"]["levels"][1] == {"level": "Level 1", "total": 1, "invested": 1}
    assert all(
        member["username"] != "outside"
        for tier in result["tiers"].values()
        for member in tier["members"]
    )


@pytest.mark.anyio
async def test_membership_breakdown_uses_only_requested_levels(db: AsyncSession) -> None:
    root = models.User(username="root-levels", password_hash="x")
    level_four = models.Plan(name="Level 4", price=250, membership_level=4, daily_tasks_limit=5, validity_days=60)
    level_five = models.Plan(name="Level 5", price=500, membership_level=5, daily_tasks_limit=5, validity_days=60)
    pro = models.Plan(name="Pro", price=1000, membership_level=6, daily_tasks_limit=5, validity_days=60)
    members = [
        models.User(username="level-four", password_hash="x", current_plan=level_four, plan_purchase_price=250, has_purchased_first_package=True),
        models.User(username="level-five", password_hash="x", current_plan=level_five, plan_purchase_price=500, has_purchased_first_package=True),
        models.User(username="pro", password_hash="x", current_plan=pro, plan_purchase_price=1000, has_purchased_first_package=True),
    ]
    db.add_all([root, level_four, level_five, pro, *members])
    await db.flush()
    db.add_all([models.ReferralRelationship(user_id=member.id, referrer_id=root.id) for member in members])
    await db.flush()

    result = await _load_referral_network(root, db)

    assert [level["level"] for level in result["tiers"]["A"]["levels"]] == [
        "Intern", "Level 1", "Level 2", "Level 3", "Level 4", "Pro"
    ]
    assert [member["membership_level"] for member in result["tiers"]["A"]["members"]] == [
        "Level 4", "Pro", "Pro"
    ]


@pytest.mark.anyio
async def test_active_referrals_batches_activity_and_preserves_three_tiers(db: AsyncSession) -> None:
    now = datetime.now(timezone.utc)
    plan = models.Plan(name="LV1", price=20, membership_level=1, daily_tasks_limit=2, validity_days=60)
    task = models.VideoTask(title="Referral task", video_url="https://example.com/video")
    root = models.User(username="active-root", password_hash="x")
    direct = models.User(username="active-direct", first_name="Direct", password_hash="x")
    second = models.User(username="active-second", first_name="Second", password_hash="x")
    third = models.User(username="active-third", first_name="Third", password_hash="x")
    db.add_all([plan, task, root, direct, second, third])
    await db.flush()

    db.add_all([
        models.ReferralRelationship(user_id=direct.id, referrer_id=root.id),
        models.ReferralRelationship(user_id=second.id, referrer_id=direct.id),
        models.ReferralRelationship(user_id=third.id, referrer_id=second.id),
        models.UserPlanHistory(
            user_id=direct.id,
            plan_id=plan.id,
            purchase_price=20,
            expires_at=now + timedelta(days=30),
            status="active",
        ),
        models.UserVideoTask(user_id=second.id, video_task_id=task.id, status="completed"),
    ])
    await db.flush()

    result = await get_active_referrals(root, db)

    assert result == [
        {"name": "Direct", "status": "Invite Sent", "tier": "A", "is_active": True},
        {"name": "Second", "status": "Accepted", "tier": "B", "is_active": False},
        {"name": "Third", "status": "Invite Sent", "tier": "C", "is_active": False},
    ]
