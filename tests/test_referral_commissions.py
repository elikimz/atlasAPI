from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import models
from app.services.referral_commissions import award_referral_commissions, effective_rate, money


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
async def test_three_tiers_membership_policy_and_idempotency(db: AsyncSession) -> None:
    low = models.Plan(name="LV1", price=20, membership_level=1, membership_commission_rate=0.10, daily_tasks_limit=1, validity_days=30)
    same = models.Plan(name="LV2", price=50, membership_level=2, membership_commission_rate=0.06, daily_tasks_limit=1, validity_days=30)
    high = models.Plan(name="LV3", price=100, membership_level=3, membership_commission_rate=0.08, daily_tasks_limit=1, validity_days=30)
    root = models.User(username="root", password_hash="x", withdrawal_wallet_balance=0, current_plan=low)
    middle = models.User(username="middle", password_hash="x", withdrawal_wallet_balance=0, current_plan=same)
    direct = models.User(username="direct", password_hash="x", withdrawal_wallet_balance=0, current_plan=high)
    buyer = models.User(username="buyer", password_hash="x", withdrawal_wallet_balance=0, current_plan=high)
    db.add_all([low, same, high, root, middle, direct, buyer])
    await db.flush()
    db.add_all([
        models.ReferralRelationship(user_id=middle.id, referrer_id=root.id),
        models.ReferralRelationship(user_id=direct.id, referrer_id=middle.id),
        models.ReferralRelationship(user_id=buyer.id, referrer_id=direct.id),
        models.ReferralCode(user_id=root.id, code="ROOT"),
        models.ReferralCode(user_id=middle.id, code="MIDDLE"),
        models.ReferralCode(user_id=direct.id, code="DIRECT"),
    ])
    await db.flush()

    # Downline is higher than root: root rate (10%) capped by Tier C (1%) => 1%.
    # Downline is higher than middle: middle rate (6%) capped by Tier B (4%) => 4%.
    # Downline is higher than direct: direct rate (8%) capped by Tier A (10%) => 8%.
    awards = await award_referral_commissions(
        db, downline=buyer, downline_plan=high, eligible_amount=80,
        source_type="plan_upgrade", source_id=123,
    )
    await db.flush()
    assert [round(award.amount, 2) for award in awards] == [6.40, 3.20, 0.80]
    assert [award.tier for award in awards] == [1, 2, 3]

    # Retrying the same source does not create another award or wallet credit.
    retry = await award_referral_commissions(
        db, downline=buyer, downline_plan=high, eligible_amount=80,
        source_type="plan_upgrade", source_id=123,
    )
    await db.flush()
    assert len(retry) == 3
    assert (await db.scalar(select(models.User).where(models.User.id == direct.id))).withdrawal_wallet_balance == pytest.approx(6.40)
    assert (await db.scalar(select(models.CommissionAward))).amount == pytest.approx(6.40)


@pytest.mark.parametrize(
    ("upline_level", "downline_level", "upline_rate", "downline_rate", "tier_rate", "expected"),
    [
        (1, 3, 0.08, 0.03, 0.10, 0.08),  # downline higher -> upline rate
        (2, 2, 0.08, 0.03, 0.10, 0.03),  # equal -> downline rate
        (3, 1, 0.08, 0.03, 0.10, 0.03),  # upline higher -> downline rate
        (1, 3, None, None, 0.04, 0.04),  # unconfigured plans preserve tier baseline
    ],
)
def test_membership_rate_selection(upline_level, downline_level, upline_rate, downline_rate, tier_rate, expected):
    upline = models.Plan(name="U", price=10, membership_level=upline_level, membership_commission_rate=upline_rate, daily_tasks_limit=1, validity_days=1)
    downline = models.Plan(name="D", price=20, membership_level=downline_level, membership_commission_rate=downline_rate, daily_tasks_limit=1, validity_days=1)
    assert effective_rate(upline, downline, money(tier_rate)) == money(expected)
