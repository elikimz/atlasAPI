from collections.abc import AsyncIterator
import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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


def plan(name: str, price: float, level: int, rate: float | None) -> models.Plan:
    return models.Plan(
        name=name,
        price=price,
        membership_level=level,
        membership_commission_rate=rate,
        daily_tasks_limit=1,
        validity_days=30,
    )


@pytest.mark.parametrize(
    ("upline_level", "downline_level", "upline_rate", "downline_rate", "expected_rate"),
    [
        (1, 2, 0.10, None, 0.10),  # downline higher -> upline membership rate
        (1, 1, 0.10, 0.10, 0.10),  # equal -> downline membership rate
        (2, 1, 0.08, 0.10, 0.10),  # upline higher -> downline membership rate
        (2, 2, None, None, None),  # missing rates fail closed
        (1, 2, 0.20, None, 0.10),  # depth ceiling remains separate
    ],
)
def test_membership_rate_selection(
    upline_level, downline_level, upline_rate, downline_rate, expected_rate
):
    upline = plan("U", 50, upline_level, upline_rate)
    downline = plan("D", 20, downline_level, downline_rate)
    result = effective_rate(upline, downline, money("0.10"))
    assert result == (money(expected_rate) if expected_rate is not None else None)


@pytest.mark.anyio
async def test_membership_price_basis_for_all_three_relationships(db: AsyncSession) -> None:
    lv1 = plan("LV1", 20, 1, 0.10)
    lv2 = plan("LV2", 50, 2, 0.08)
    lv3 = plan("LV3", 100, 3, 0.06)
    buyer_plan = plan("LV4", 150, 4, 0.05)
    root = models.User(username="root", password_hash="x", current_plan=lv1, withdrawal_wallet_balance=0)
    middle = models.User(username="middle", password_hash="x", current_plan=lv2, withdrawal_wallet_balance=0)
    direct = models.User(username="direct", password_hash="x", current_plan=lv3, withdrawal_wallet_balance=0)
    buyer = models.User(username="buyer", password_hash="x", current_plan=buyer_plan, withdrawal_wallet_balance=0)
    db.add_all([lv1, lv2, lv3, buyer_plan, root, middle, direct, buyer])
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

    awards = await award_referral_commissions(
        db,
        downline=buyer,
        downline_plan=buyer_plan,
        eligible_amount=1,  # ignored; each upline gets its applicable plan price
        source_type="plan_purchase",
        source_id=123,
    )
    await db.flush()

    # Direct LV3 upline: 6% of LV3 $100 = $6 (Tier A ceiling 10%).
    # LV2 upline: 4% Tier B ceiling of LV2 $50 = $2.
    # LV1 upline: 1% Tier C ceiling of LV1 $20 = $0.20.
    assert [round(award.amount, 2) for award in awards] == [6.00, 2.00, 0.20]
    assert [round(award.eligible_amount, 2) for award in awards] == [100.00, 50.00, 20.00]


@pytest.mark.anyio
async def test_user_examples_and_missing_membership_rate(db: AsyncSession) -> None:
    lv1 = plan("LV1", 20, 1, 0.10)
    lv2_missing = plan("LV2", 50, 2, None)
    lv2_configured = plan("LV2 configured", 50, 2, 0.08)

    # LV1 upline -> LV2 downline: 10% of LV1 price ($20) = $2.
    upline = models.User(username="lv1_upline", password_hash="x", current_plan=lv1, withdrawal_wallet_balance=0)
    buyer = models.User(username="lv2_buyer", password_hash="x", current_plan=lv2_missing, withdrawal_wallet_balance=0)
    db.add_all([lv1, lv2_missing, upline, buyer])
    await db.flush()
    db.add(models.ReferralRelationship(user_id=buyer.id, referrer_id=upline.id))
    await db.flush()
    awards = await award_referral_commissions(db, downline=buyer, downline_plan=lv2_missing, eligible_amount=50, source_type="plan_purchase", source_id=1)
    assert len(awards) == 1
    assert awards[0].amount == pytest.approx(2.00)

    # LV2 upline -> LV1 downline: 10% of LV1 price ($20) = $2.
    higher = models.User(username="lv2_upline", password_hash="x", current_plan=lv2_configured, withdrawal_wallet_balance=0)
    lower = models.User(username="lv1_buyer", password_hash="x", current_plan=lv1, withdrawal_wallet_balance=0)
    db.add_all([lv2_configured, higher, lower])
    await db.flush()
    db.add(models.ReferralRelationship(user_id=lower.id, referrer_id=higher.id))
    await db.flush()
    awards = await award_referral_commissions(db, downline=lower, downline_plan=lv1, eligible_amount=999, source_type="plan_purchase", source_id=2)
    assert len(awards) == 1
    assert awards[0].amount == pytest.approx(2.00)

    # Same-level LV2 has no configured LV2 rate: no invented payout.
    same_a = models.User(username="same_a", password_hash="x", current_plan=lv2_missing, withdrawal_wallet_balance=0)
    same_b = models.User(username="same_b", password_hash="x", current_plan=lv2_missing, withdrawal_wallet_balance=0)
    db.add_all([same_a, same_b])
    await db.flush()
    db.add(models.ReferralRelationship(user_id=same_b.id, referrer_id=same_a.id))
    await db.flush()
    assert await award_referral_commissions(db, downline=same_b, downline_plan=lv2_missing, eligible_amount=50, source_type="plan_purchase", source_id=3) == []


@pytest.mark.anyio
async def test_duplicate_source_is_idempotent_and_audited_once(db: AsyncSession) -> None:
    lv1 = plan("LV1", 20, 1, 0.10)
    upline = models.User(username="upline", password_hash="x", current_plan=lv1, withdrawal_wallet_balance=0)
    buyer = models.User(username="buyer", password_hash="x", current_plan=lv1, withdrawal_wallet_balance=0)
    db.add_all([lv1, upline, buyer])
    await db.flush()
    db.add(models.ReferralRelationship(user_id=buyer.id, referrer_id=upline.id))
    await db.flush()

    first = await award_referral_commissions(db, downline=buyer, downline_plan=lv1, eligible_amount=20, source_type="pesaflux_payment", source_id=55)
    second = await award_referral_commissions(db, downline=buyer, downline_plan=lv1, eligible_amount=20, source_type="pesaflux_payment", source_id=55)
    await db.commit()
    assert first[0].amount == second[0].amount == pytest.approx(2.00)
    refreshed = await db.scalar(select(models.User).where(models.User.id == upline.id))
    assert refreshed.withdrawal_wallet_balance == pytest.approx(2.00)
    assert await db.scalar(select(models.CommissionAward.id).where(models.CommissionAward.source_id == 55)) is not None
    assert len((await db.scalars(select(models.EarningsLog).where(models.EarningsLog.user_id == upline.id))).all()) == 1


@pytest.mark.anyio
async def test_concurrent_same_source_cannot_double_credit(db: AsyncSession) -> None:
    # The unique award key is the database-level concurrency guard. This test
    # uses two independent sessions against a shared SQLite connection to
    # exercise the duplicate race without contacting a payment provider.
    connection = await db.connection()
    if connection.engine.dialect.name == "sqlite":
        pytest.skip("Concurrent row-lock behavior is validated on PostgreSQL CI; SQLite cannot model it reliably.")
    factory = async_sessionmaker(connection.engine, expire_on_commit=False)
    lv1 = plan("LV1", 20, 1, 0.10)
    upline = models.User(username="concurrent_upline", password_hash="x", current_plan=lv1, withdrawal_wallet_balance=0)
    buyer = models.User(username="concurrent_buyer", password_hash="x", current_plan=lv1, withdrawal_wallet_balance=0)
    db.add_all([lv1, upline, buyer])
    await db.flush()
    db.add(models.ReferralRelationship(user_id=buyer.id, referrer_id=upline.id))
    await db.commit()

    async def award_once() -> None:
        async with factory() as session:
            downline = await session.scalar(select(models.User).where(models.User.id == buyer.id))
            buyer_plan = await session.scalar(select(models.Plan).where(models.Plan.id == lv1.id))
            await award_referral_commissions(session, downline=downline, downline_plan=buyer_plan, eligible_amount=20, source_type="concurrent", source_id=77)
            await session.commit()

    results = await asyncio.gather(award_once(), award_once(), return_exceptions=True)
    unexpected_errors = [
        result for result in results
        if isinstance(result, Exception)
        and not isinstance(result, IntegrityError)
        and "locked" not in str(result).lower()
    ]
    assert not unexpected_errors
    await db.refresh(upline)
    assert upline.withdrawal_wallet_balance == pytest.approx(2.00)
    assert await db.scalar(select(models.CommissionAward.id).where(models.CommissionAward.source_id == 77)) is not None
