"""Centralized referral commission calculation and awarding.

The configured tier percentages remain the payout ceiling. Each plan may set a
membership_commission_rate (as a decimal fraction); when it is NULL, the tier
baseline is used, preserving existing behavior until business rates are
explicitly configured. A unique award key makes retries and duplicate provider
callbacks harmless.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import models

CENT = Decimal("0.01")
TIER_RATES = (Decimal("0.10"), Decimal("0.04"), Decimal("0.01"))
TIER_FIELDS = ("tier_a_invite_earnings", "tier_b_invite_earnings", "tier_c_invite_earnings")


def money(value: object) -> Decimal:
    return Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def plan_level(plan: models.Plan | None) -> int:
    if plan is None:
        return 0
    if plan.membership_level is not None:
        return int(plan.membership_level)
    name = (plan.name or "").strip().lower()
    if name == "intern":
        return 0
    digits = "".join(ch for ch in name if ch.isdigit())
    # Existing deployments may contain paid plans with arbitrary names such as
    # "Starter". Treat any positive-price plan as a valid base membership when
    # no explicit hierarchy has been configured.
    return int(digits) if digits else (1 if money(plan.price) > 0 else 0)


def configured_membership_rate(plan: models.Plan | None, tier_rate: Decimal) -> Decimal:
    configured = getattr(plan, "membership_commission_rate", None) if plan else None
    if configured is None:
        return tier_rate
    return max(Decimal("0"), min(Decimal("1"), Decimal(str(configured))))


def effective_rate(upline_plan: models.Plan | None, downline_plan: models.Plan | None, tier_rate: Decimal) -> Decimal:
    """Apply membership policy without exceeding the tier baseline.

    If the downline is above the upline, the upline's configured rate applies.
    For equal levels the downline rate applies. If the upline is above the
    downline, the downline rate applies. In every case it is capped by tier_rate.
    """
    upline_level = plan_level(upline_plan)
    downline_level = plan_level(downline_plan)
    selected = upline_plan if downline_level > upline_level else downline_plan
    return min(tier_rate, configured_membership_rate(selected, tier_rate))


async def _uplines(db: AsyncSession, downline_id: int) -> Iterable[tuple[int, int]]:
    current_id = downline_id
    for tier in range(1, 4):
        rel = await db.scalar(select(models.ReferralRelationship).where(models.ReferralRelationship.user_id == current_id))
        if not rel:
            break
        yield tier, rel.referrer_id
        current_id = rel.referrer_id


async def award_referral_commissions(
    db: AsyncSession,
    *,
    downline: models.User,
    downline_plan: models.Plan,
    eligible_amount: object,
    source_type: str,
    source_id: int,
) -> list[models.CommissionAward]:
    """Award at most one commission per source/upline/tier.

    The caller owns the surrounding transaction. A uniqueness constraint is the
    final protection against duplicate callbacks; an existing award is treated
    as an idempotent success.
    """
    amount = money(eligible_amount)
    if amount <= 0 or plan_level(downline_plan) <= 0:
        return []

    awards: list[models.CommissionAward] = []
    async for tier, upline_id in _uplines(db, downline.id):
        upline = await db.scalar(select(models.User).where(models.User.id == upline_id))
        if not upline:
            continue
        upline_plan = await db.scalar(select(models.Plan).where(models.Plan.id == upline.current_plan_id)) if upline.current_plan_id else None
        tier_rate = TIER_RATES[tier - 1]
        rate = effective_rate(upline_plan, downline_plan, tier_rate)
        commission = (amount * rate).quantize(CENT, rounding=ROUND_HALF_UP)
        if commission <= 0:
            continue

        existing = await db.scalar(select(models.CommissionAward).where(
            models.CommissionAward.source_type == source_type,
            models.CommissionAward.source_id == source_id,
            models.CommissionAward.upline_user_id == upline.id,
            models.CommissionAward.tier == tier,
        ))
        if existing:
            awards.append(existing)
            continue

        award = models.CommissionAward(
            source_type=source_type,
            source_id=source_id,
            downline_user_id=downline.id,
            upline_user_id=upline.id,
            tier=tier,
            eligible_amount=float(amount),
            rate=float(rate),
            amount=float(commission),
        )
        try:
            async with db.begin_nested():
                db.add(award)
                await db.flush()
        except IntegrityError:
            # Another callback won the unique-key race. Treat it as an
            # idempotent retry and leave the existing wallet credit untouched.
            existing = await db.scalar(select(models.CommissionAward).where(
                models.CommissionAward.source_type == source_type,
                models.CommissionAward.source_id == source_id,
                models.CommissionAward.upline_user_id == upline.id,
                models.CommissionAward.tier == tier,
            ))
            if existing:
                awards.append(existing)
                continue
            raise
        upline.withdrawal_wallet_balance = float(money(upline.withdrawal_wallet_balance) + commission)
        field = TIER_FIELDS[tier - 1]
        code = await db.scalar(select(models.ReferralCode).where(models.ReferralCode.user_id == upline.id).limit(1))
        if code:
            setattr(code, field, float(money(getattr(code, field, 0)) + commission))
            code.earned_amount = float(money(code.earned_amount) + commission)
        db.add(models.EarningsLog(
            user_id=upline.id,
            amount=float(commission),
            type="invite_commission",
            description=f"Tier {tier} referral commission from {downline.email or downline.username}",
        ))
        awards.append(award)
    return awards
