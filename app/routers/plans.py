from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from datetime import datetime, timedelta, timezone

from app.database.database import get_async_db
from app.models import models
from app.schemas import plan as plan_schemas
from app.routers.auth import get_current_user as get_current_active_user
from app.services.cache import CacheKeys, CacheTTL, cache, invalidate_shared_cache, invalidate_user_cache

router = APIRouter(
    prefix="/plans",
    tags=["plans"]
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _plan_is_active(user: models.User) -> bool:
    expiry_date = _as_utc(user.plan_expiry_date)
    return bool(user.current_plan_id and expiry_date and expiry_date > _utc_now())


def _plan_is_expired(user: models.User) -> bool:
    expiry_date = _as_utc(user.plan_expiry_date)
    return bool(user.current_plan_id and (expiry_date is None or expiry_date <= _utc_now()))


@router.get("", response_model=list[plan_schemas.Plan])
async def get_all_plans(db: AsyncSession = Depends(get_async_db)):
    """Return the shared active-plan catalog from a 30-minute cache."""
    async def load_active_plans() -> list[dict]:
        result = await db.execute(select(models.Plan).where(models.Plan.is_active.is_(True)))
        return [
            {
                "id": plan.id,
                "name": plan.name,
                "price": plan.price,
                "daily_tasks_limit": plan.daily_tasks_limit,
                "validity_days": plan.validity_days,
                "description": plan.description,
                "is_active": plan.is_active,
                "is_upgrade_only": plan.is_upgrade_only,
            }
            for plan in result.scalars().all()
        ]

    return await cache.get_or_set(CacheKeys.plans(), CacheTTL.PLANS, load_active_plans)


@router.post("/purchase/{plan_id}")
async def purchase_plan(
    plan_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: models.User = Depends(get_current_active_user)
):
    """
    Purchase a new plan (first-time or after expiry).

    Deposit Wallet Rule: plan price is deducted from deposit_wallet_balance only.
    Invite Commission Rule: commissions are generated ONLY on first-time / initial purchase.
    """
    result = await db.execute(select(models.Plan).filter(models.Plan.id == plan_id))
    plan = result.scalar_one_or_none()
    if not plan or not plan.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")

    if _plan_is_active(current_user):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User already has an active plan. Please upgrade instead."
        )

    if _plan_is_expired(current_user):
        expired_result = await db.execute(select(models.Plan).filter(models.Plan.id == current_user.current_plan_id))
        expired_plan = expired_result.scalar_one_or_none()
        if expired_plan and plan.price <= expired_plan.price:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Your previous plan has expired. You must upgrade to a higher tier."
            )

    # Deposit Wallet: only deduct from deposit_wallet_balance (never from earnings)
    if plan.price > 0 and (current_user.deposit_wallet_balance or 0) < plan.price:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Insufficient deposit wallet balance to purchase this plan."
        )

    now = _utc_now()
    # Deduct from deposit wallet only
    current_user.deposit_wallet_balance -= plan.price
    current_user.current_plan_id = plan.id
    current_user.plan_purchase_price = plan.price
    current_user.plan_start_date = now
    current_user.plan_expiry_date = now + timedelta(days=plan.validity_days)

    user_plan_history = models.UserPlanHistory(
        user_id=current_user.id,
        plan_id=plan.id,
        purchase_price=plan.price,
        purchased_at=current_user.plan_start_date,
        expires_at=current_user.plan_expiry_date,
        status="active",
        refunded_amount=0.0
    )
    db.add(user_plan_history)
    db.add(current_user)

    # ─────────────────────────────────────────────────────────────────────────
    # Multi-Tier Invite Commission Rule (CRITICAL):
    # Commissions are generated ONLY on the INITIAL / first-time plan purchase.
    # If has_purchased_first_package is already True this is a re-purchase after
    # expiry and NO commission is generated (treat same as upgrade — skip).
    # ─────────────────────────────────────────────────────────────────────────
    if not current_user.has_purchased_first_package and plan.price > 0:
        current_user.has_purchased_first_package = True

        # 3-Tier Commission Split: Tier A (10%), Tier B (4%), Tier C (1%)
        commission_config = [
            ("tier_a_invite_earnings", 0.10),
            ("tier_b_invite_earnings", 0.04),
            ("tier_c_invite_earnings", 0.01),
        ]

        rel_result = await db.execute(
            select(models.ReferralRelationship).filter(
                models.ReferralRelationship.user_id == current_user.id
            )
        )
        rel = rel_result.scalar_one_or_none()
        current_upline_id = rel.referrer_id if rel else None
        affected_upline_ids: list[int] = []

        for field_name, percentage in commission_config:
            if not current_upline_id:
                break

            upline_result = await db.execute(
                select(models.User).filter(models.User.id == current_upline_id)
            )
            upline = upline_result.scalar_one_or_none()

            if upline:
                affected_upline_ids.append(upline.id)
                commission_amount = plan.price * percentage
                # Credit commission to upline's withdrawal wallet (cashable earnings)
                upline.withdrawal_wallet_balance = (upline.withdrawal_wallet_balance or 0.0) + commission_amount

                # Log to EarningsLog for GMT-based period calculations
                db.add(models.EarningsLog(
                    user_id=upline.id,
                    amount=commission_amount,
                    type="invite_commission",
                    description=f"Invite commission from {current_user.email} (Tier {field_name.split('_')[1].upper()})"
                ))

                # Update upline's referral code stats
                code_result = await db.execute(
                    select(models.ReferralCode)
                    .filter(models.ReferralCode.user_id == upline.id)
                    .limit(1)
                )
                ref_code = code_result.scalar_one_or_none()
                if ref_code:
                    current_val = getattr(ref_code, field_name, 0.0) or 0.0
                    setattr(ref_code, field_name, current_val + commission_amount)
                    # Also update legacy total
                    ref_code.earned_amount = (ref_code.earned_amount or 0.0) + commission_amount

                # Move up to next tier
                next_rel_result = await db.execute(
                    select(models.ReferralRelationship).filter(
                        models.ReferralRelationship.user_id == upline.id
                    )
                )
                next_rel = next_rel_result.scalar_one_or_none()
                current_upline_id = next_rel.referrer_id if next_rel else None
            else:
                break

    # Auto-assign tasks for the new plan.
    # Include global tasks (plan_id IS NULL) AND tasks specific to the plan.
    purchase_task_filter = (models.VideoTask.plan_id.is_(None)) | (models.VideoTask.plan_id == plan.id)
    result_tasks = await db.execute(
        select(models.VideoTask).filter(purchase_task_filter)
    )
    plan_tasks = result_tasks.scalars().all()
    for task in plan_tasks:
        existing_task_result = await db.execute(
            select(models.UserVideoTask).filter(
                models.UserVideoTask.user_id == current_user.id,
                models.UserVideoTask.video_task_id == task.id
            )
        )
        if not existing_task_result.scalar_one_or_none():
            db.add(models.UserVideoTask(
                user_id=current_user.id,
                video_task_id=task.id,
                status="pending"
            ))

    await db.commit()
    await db.refresh(user_plan_history)

    # A purchase changes the buyer's wallet, current plan, task assignment, and
    # potentially the wallet/referral aggregates of each upline.
    await invalidate_user_cache(current_user.id, "tasks", "dashboard", "referrals", "payments")
    for upline_id in locals().get("affected_upline_ids", []):
        await invalidate_user_cache(upline_id, "dashboard", "referrals", "payments")
    await invalidate_shared_cache("admin_stats")

    # Return user plan history along with updated user balances
    user_result = await db.execute(
        select(models.User)
        .options(selectinload(models.User.current_plan))
        .filter(models.User.id == current_user.id)
    )
    refreshed_user = user_result.scalar_one()

    return {
        "plan_history": user_plan_history,
        "user": {
            "id": refreshed_user.id,
            "current_plan_id": refreshed_user.current_plan_id,
            "deposit_wallet_balance": refreshed_user.deposit_wallet_balance or 0.0,
            "withdrawal_wallet_balance": refreshed_user.withdrawal_wallet_balance or 0.0,
        }
    }


@router.post("/upgrade/{new_plan_id}")
async def upgrade_plan(
    new_plan_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: models.User = Depends(get_current_active_user)
):
    """
    Upgrade to a higher-tier plan.

    Deposit Wallet Rule: the FULL price of the new plan is deducted from deposit_wallet_balance.
    The old plan price is refunded IMMEDIATELY to the Withdrawal Wallet.

    Invite Commission Rule (CRITICAL):
    - Plan upgrades NEVER generate invite commissions for the upline.
    """
    result = await db.execute(select(models.Plan).filter(models.Plan.id == new_plan_id))
    new_plan = result.scalar_one_or_none()
    if not new_plan or not new_plan.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="New plan not found")

    if not current_user.current_plan_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No existing plan to upgrade from. Please purchase a plan first."
        )

    result = await db.execute(
        select(models.Plan).filter(models.Plan.id == current_user.current_plan_id)
    )
    current_plan = result.scalar_one_or_none()
    if not current_plan:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Current plan not found in database."
        )

    if new_plan.price <= current_plan.price:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New plan must be a higher tier than the current plan."
        )

    # Find the active plan history entry to get the exact purchase price for refund
    result = await db.execute(
        select(models.UserPlanHistory)
        .filter(
            models.UserPlanHistory.user_id == current_user.id,
            models.UserPlanHistory.plan_id == current_plan.id,
            models.UserPlanHistory.status == "active"
        )
        .order_by(models.UserPlanHistory.purchased_at.desc())
    )
    old_user_plan_entry = result.scalars().first()
    refund_amount = (
        old_user_plan_entry.purchase_price
        if old_user_plan_entry
        else current_user.plan_purchase_price or 0.0
    )

    # RULE: Full price of the new plan is deducted from the deposit wallet
    if (current_user.deposit_wallet_balance or 0) < new_plan.price:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Insufficient deposit wallet balance to upgrade. "
                f"Required: ${new_plan.price:.2f}. "
                f"Your previous plan price (${refund_amount:.2f}) will be refunded "
                f"immediately to your withdrawal wallet."
            )
        )

    now = _utc_now()

    # Deduct the FULL price of the new plan from the deposit wallet
    current_user.deposit_wallet_balance = (current_user.deposit_wallet_balance or 0.0) - new_plan.price

    # Mark first-purchase flag
    if new_plan.price > 0:
        current_user.has_purchased_first_package = True

    # Mark old plan history as upgraded
    if old_user_plan_entry:
        old_user_plan_entry.status = "upgraded"
        old_user_plan_entry.refunded_amount = refund_amount
        db.add(old_user_plan_entry)

    # ─────────────────────────────────────────────────────────────────────────
    # IMMEDIATE Refund Mechanism for Upgrade
    # Credit the amount to withdrawal_wallet_balance IMMEDIATELY.
    # ─────────────────────────────────────────────────────────────────────────
    if refund_amount > 0:
        # Credit to withdrawal wallet (cashable)
        current_user.withdrawal_wallet_balance = (current_user.withdrawal_wallet_balance or 0.0) + refund_amount
        db.add(current_user)  # Ensure user state is saved
        
        # Log to EarningsLog for period tracking
        db.add(models.EarningsLog(
            user_id=current_user.id,
            amount=refund_amount,
            type="upgrade_refund",
            description=f"Immediate upgrade refund for previous plan"
        ))
        
        # Also log in upgrade_refunds table for audit trail (marked as released)
        upgrade_refund = models.UpgradeRefund(
            user_id=current_user.id,
            amount=refund_amount,
            status="released",
            release_at=now,
            released_at=now,
            plan_history_id=old_user_plan_entry.id if old_user_plan_entry else None,
        )
        db.add(upgrade_refund)

    # Switch user to new plan
    current_user.current_plan_id = new_plan.id
    current_user.plan_purchase_price = new_plan.price
    current_user.plan_start_date = now
    current_user.plan_expiry_date = now + timedelta(days=new_plan.validity_days)

    new_user_plan_history = models.UserPlanHistory(
        user_id=current_user.id,
        plan_id=new_plan.id,
        purchase_price=new_plan.price,
        purchased_at=current_user.plan_start_date,
        expires_at=current_user.plan_expiry_date,
        status="active",
        refunded_amount=0.0
    )
    db.add(new_user_plan_history)
    db.add(current_user)

    # Clean up old plan's pending tasks
    await db.execute(
        models.UserVideoTask.__table__.delete().where(
            models.UserVideoTask.user_id == current_user.id,
            models.UserVideoTask.status == "pending"
        )
    )

    # Auto-assign tasks for the new upgraded plan.
    # Include global tasks (plan_id IS NULL) AND tasks specific to the new plan.
    # For non-Intern plans, users should see both global and plan-specific tasks.
    task_filter = (models.VideoTask.plan_id.is_(None)) | (models.VideoTask.plan_id == new_plan.id)
    result_tasks = await db.execute(
        select(models.VideoTask).filter(task_filter)
    )
    plan_tasks = result_tasks.scalars().all()
    for task in plan_tasks:
        # Check if user already has this task (e.g. a global task assigned previously)
        existing_result = await db.execute(
            select(models.UserVideoTask).filter(
                models.UserVideoTask.user_id == current_user.id,
                models.UserVideoTask.video_task_id == task.id,
            )
        )
        if not existing_result.scalar_one_or_none():
            db.add(models.UserVideoTask(
                user_id=current_user.id,
                video_task_id=task.id,
                status="pending"
            ))

    await db.commit()
    await db.refresh(new_user_plan_history)

    await invalidate_user_cache(current_user.id, "tasks", "dashboard", "referrals", "payments")
    await invalidate_shared_cache("admin_stats")

    # Return user plan history along with updated user balances
    user_result = await db.execute(
        select(models.User)
        .options(selectinload(models.User.current_plan))
        .filter(models.User.id == current_user.id)
    )
    refreshed_user = user_result.scalar_one()

    return {
        "plan_history": new_user_plan_history,
        "user": {
            "id": refreshed_user.id,
            "current_plan_id": refreshed_user.current_plan_id,
            "deposit_wallet_balance": refreshed_user.deposit_wallet_balance or 0.0,
            "withdrawal_wallet_balance": refreshed_user.withdrawal_wallet_balance or 0.0,
        }
    }


@router.post("/release-refunds", response_model=dict)
async def release_pending_refunds(
    db: AsyncSession = Depends(get_async_db),
    current_user: models.User = Depends(get_current_active_user)
):
    """
    Legacy endpoint - kept for backward compatibility.
    Check for and release any legacy upgrade refunds that have passed their 72h lock.
    """
    now = _utc_now()
    query = select(models.UpgradeRefund).filter(
        models.UpgradeRefund.user_id == current_user.id,
        models.UpgradeRefund.status == "pending",
        models.UpgradeRefund.release_at <= now
    )
    result = await db.execute(query)
    pending_refunds = result.scalars().all()

    released_total = 0.0
    for refund in pending_refunds:
        refund.status = "released"
        refund.released_at = now
        released_total += refund.amount
        
        # Credit to withdrawal wallet (cashable)
        current_user.withdrawal_wallet_balance = (current_user.withdrawal_wallet_balance or 0.0) + refund.amount
        
        # Log to EarningsLog for period tracking
        db.add(models.EarningsLog(
            user_id=current_user.id,
            amount=refund.amount,
            type="upgrade_refund",
            description=f"Released legacy upgrade refund for previous plan"
        ))

    if released_total > 0:
        db.add(current_user)
        await db.commit()
        await invalidate_user_cache(current_user.id, "dashboard", "payments", "referrals")
        await invalidate_shared_cache("admin_stats")

    return {
        "message": f"Released {len(pending_refunds)} legacy refunds totaling ${released_total:.2f}",
        "released_amount": released_total
    }
