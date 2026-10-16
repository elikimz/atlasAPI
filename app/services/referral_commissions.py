"""Membership-level referral commission calculation and awarding.

Membership levels and referral depth tiers are separate:
- Membership level selects the commission rate and price basis.
- Referral depth supplies the existing Tier A/B/C maximum ceilings.

A missing membership rate fails closed: no commission is created until an
administrator configures that level. This avoids inventing financial values.
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
    return int(digits) if digits else (1 if money(plan.price) > 0 else 0)


def applicable_plan(upline_plan: models.Plan | None, downline_plan: models.Plan) -> models.Plan | None:
    """Return the plan whose membership rate and price basis apply to this pair."""
    if plan_level(downline_plan) > plan_level(upline_plan):
        return upline_plan
    return downline_plan


def configured_membership_rate(plan: models.Plan | None) -> Decimal | None:
    """Return an explicitly configured membership rate; never invent a fallback."""
    if plan is None or plan.membership_commission_rate is None:
        return None
    return max(Decimal("0"), min(Decimal("1"), Decimal(str(plan.membership_commission_rate))))


def effective_rate(
    upline_plan: models.Plan | None,
    downline_plan: models.Plan | None,
    tier_rate: Decimal,
) -> Decimal | None:
    if downline_plan is None:
        return None
    selected = applicable_plan(upline_plan, downline_plan)
    membership_rate = configured_membership_rate(selected)
    if membership_rate is None:
        return None
    # Tier baselines remain depth ceilings, not membership-level rates.
    return min(tier_rate, membership_rate)


async def _uplines(db: AsyncSession, downline_id: int) -> Iterable[tuple[int, int]]:
    current_id = downline_id
    for tier in range(1, 4):
        rel = await db.scalar(
            select(models.ReferralRelationship).where(models.ReferralRelationship.user_id == current_id)
        )
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
    """Award one idempotent commission per source/upline/depth.

    ``eligible_amount`` is retained for call-site compatibility but is no
    longer used as the commission base. Each upline gets the price of the
    applicable membership plan independently, as required by policy.
    """
    if plan_level(downline_plan) <= 0:
        return []

    awards: list[models.CommissionAward] = []
    async for tier, upline_id in _uplines(db, downline.id):
        # Lock the recipient row so concurrent successful purchases cannot
        # overwrite one another's wallet balance on PostgreSQL.
        upline = await db.scalar(
            select(models.User).where(models.User.id == upline_id).with_for_update()
        )
        if not upline:
            continue
        upline_plan = (
            await db.scalar(select(models.Plan).where(models.Plan.id == upline.current_plan_id))
            if upline.current_plan_id
            else None
        )
        selected_plan = applicable_plan(upline_plan, downline_plan)
        rate = effective_rate(upline_plan, downline_plan, TIER_RATES[tier - 1])
        if selected_plan is None or rate is None:
            # Missing LV2/LV3/LV4 rates are deliberately not guessed.
            continue
        base_amount = money(selected_plan.price)
        commission = (base_amount * rate).quantize(CENT, rounding=ROUND_HALF_UP)
        if commission <= 0:
            continue

        existing = await db.scalar(
            select(models.CommissionAward).where(
                models.CommissionAward.source_type == source_type,
                models.CommissionAward.source_id == source_id,
                models.CommissionAward.upline_user_id == upline.id,
                models.CommissionAward.tier == tier,
            )
        )
        if existing:
            awards.append(existing)
            continue

        award = models.CommissionAward(
            source_type=source_type,
            source_id=source_id,
            downline_user_id=downline.id,
            upline_user_id=upline.id,
            tier=tier,
            eligible_amount=float(base_amount),
            rate=float(rate),
            amount=float(commission),
        )
        try:
            async with db.begin_nested():
                db.add(award)
                await db.flush()
        except IntegrityError:
            existing = await db.scalar(
                select(models.CommissionAward).where(
                    models.CommissionAward.source_type == source_type,
                    models.CommissionAward.source_id == source_id,
                    models.CommissionAward.upline_user_id == upline.id,
                    models.CommissionAward.tier == tier,
                )
            )
            if existing:
                awards.append(existing)
                continue
            raise

        upline.withdrawal_wallet_balance = float(money(upline.withdrawal_wallet_balance) + commission)
        field = TIER_FIELDS[tier - 1]
        code = await db.scalar(
            select(models.ReferralCode).where(models.ReferralCode.user_id == upline.id).limit(1)
        )
        if code:
            setattr(code, field, float(money(getattr(code, field, 0)) + commission))
            code.earned_amount = float(money(code.earned_amount) + commission)
        db.add(
            models.EarningsLog(
                user_id=upline.id,
                amount=float(commission),
                type="invite_commission",
                description=(
                    f"Tier {tier} membership commission from "
                    f"{downline.email or downline.username}; base {base_amount}"
                ),
            )
        )
        awards.append(award)
    return awards
