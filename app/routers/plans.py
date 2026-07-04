from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from datetime import datetime, timedelta, timezone

from app.database.database import get_async_db
from app.models import models
from app.schemas import plan as plan_schemas
from app.routers.auth import get_current_user as get_current_active_user

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
    result = await db.execute(select(models.Plan).where(models.Plan.is_active == True))  # noqa: E712
    plans = result.scalars().all()
    return plans


@router.post("/purchase/{plan_id}", response_model=plan_schemas.UserPlanHistory)
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
    if plan.price > 0 and current_user.deposit_wallet_balance < plan.price:
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

        for field_name, percentage in commission_config:
            if not current_upline_id:
                break

            upline_result = await db.execute(
                select(models.User).filter(models.User.id == current_upline_id)
            )
            upline = upline_result.scalar_one_or_none()

            if upline:
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

    # Auto-assign tasks for the new plan
    result_tasks = await db.execute(
        select(models.VideoTask).filter(models.VideoTask.plan_id == plan.id)
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
    await db.execute(
        select(models.User)
        .options(selectinload(models.User.current_plan))
        .filter(models.User.id == current_user.id)
    )

    return user_plan_history


@router.post("/upgrade/{new_plan_id}", response_model=plan_schemas.UserPlanHistory)
async def upgrade_plan(
    new_plan_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: models.User = Depends(get_current_active_user)
):
    """
    Upgrade to a higher-tier plan.

    Deposit Wallet Rule: only the NET additional cost (new_price - old_price) is
    deducted from deposit_wallet_balance. The old plan price is NOT immediately
    returned to any wallet.

    Upgrade Bonus Refund (3-Day Lock):
    - The old plan's purchase price is logged in upgrade_refunds with status='pending'.
    - release_at is set to exactly 72 hours from now.
    - The amount is NOT added to withdrawal_wallet_balance or performance_bonus_balance yet.
    - A background task (or the /plans/release-refunds endpoint) will release it after 72h.

    Invite Commission Rule (CRITICAL):
    - Plan upgrades NEVER generate invite commissions for the upline.
    - Only the initial first-time purchase triggers commissions.
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

    # Net additional cost: user only pays the difference
    required_additional = max(new_plan.price - refund_amount, 0.0)

    if current_user.deposit_wallet_balance < required_additional:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Insufficient deposit wallet balance to upgrade. "
                f"Required additional: ${required_additional:.2f}. "
                f"Your previous plan price (${refund_amount:.2f}) will be refunded "
                f"to your withdrawal wallet after a 3-day lock period."
            )
        )

    now = _utc_now()

    # Deduct only the net additional cost from the deposit wallet
    current_user.deposit_wallet_balance -= required_additional

    # Mark first-purchase flag (in case they somehow skipped purchase)
    if new_plan.price > 0:
        current_user.has_purchased_first_package = True

    # Mark old plan history as upgraded
    if old_user_plan_entry:
        old_user_plan_entry.status = "upgraded"
        old_user_plan_entry.refunded_amount = refund_amount
        db.add(old_user_plan_entry)

    # ─────────────────────────────────────────────────────────────────────────
    # 3-Day Lock Mechanism for Upgrade Refund
    # Log the refund as 'pending' — do NOT credit to any wallet yet.
    # The amount will be released to withdrawal_wallet_balance after 72 hours.
    # ─────────────────────────────────────────────────────────────────────────
    if refund_amount > 0:
        upgrade_refund = models.UpgradeRefund(
            user_id=current_user.id,
            amount=refund_amount,
            status="pending",
            release_at=now + timedelta(hours=72),
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

    # Auto-assign tasks for the new upgraded plan
    result_tasks = await db.execute(
        select(models.VideoTask).filter(models.VideoTask.plan_id == new_plan.id)
    )
    plan_tasks = result_tasks.scalars().all()
    for task in plan_tasks:
        db.add(models.UserVideoTask(
            user_id=current_user.id,
            video_task_id=task.id,
            status="pending"
        ))

    await db.commit()
    await db.refresh(new_user_plan_history)
    await db.execute(
        select(models.User)
        .options(selectinload(models.User.current_plan))
        .filter(models.User.id == current_user.id)
    )

    return new_user_plan_history


@router.post("/release-refunds", response_model=dict)
async def release_pending_refunds(
    db: AsyncSession = Depends(get_async_db),
    current_user: models.User = Depends(get_current_active_user)
):
    """
    Check and release any upgrade refunds that have passed the 72-hour lock period
    for the currently authenticated user.

    This endpoint is called by the frontend on page load / dashboard refresh so
    that released refunds are credited promptly without requiring a separate
    background worker.
    """
    now = _utc_now()

    result = await db.execute(
        select(models.UpgradeRefund).filter(
            models.UpgradeRefund.user_id == current_user.id,
            models.UpgradeRefund.status == "pending",
            models.UpgradeRefund.release_at <= now
        )
    )
    due_refunds = result.scalars().all()

    total_released = 0.0
    for refund in due_refunds:
        refund.status = "released"
        refund.released_at = now
        # Credit to withdrawal wallet (cashable earnings)
        current_user.withdrawal_wallet_balance = (
            current_user.withdrawal_wallet_balance or 0.0
        ) + refund.amount
        total_released += refund.amount

        # Log to EarningsLog for GMT-based period calculations
        db.add(models.EarningsLog(
            user_id=current_user.id,
            amount=refund.amount,
            type="upgrade_refund",
            description="Released upgrade refund after 3-day lock"
        ))

    if due_refunds:
        db.add(current_user)
        await db.commit()

    return {
        "released_count": len(due_refunds),
        "total_released": total_released,
        "message": (
            f"Released {len(due_refunds)} refund(s) totalling ${total_released:.2f} "
            "to your withdrawal wallet."
            if due_refunds
            else "No pending refunds due for release."
        )
    }


@router.get("/upgrade-refunds", response_model=list[dict])
async def get_upgrade_refunds(
    db: AsyncSession = Depends(get_async_db),
    current_user: models.User = Depends(get_current_active_user)
):
    """
    Return all upgrade refund records for the current user so the frontend
    can display pending (locked) vs released refund statuses.
    """
    result = await db.execute(
        select(models.UpgradeRefund)
        .filter(models.UpgradeRefund.user_id == current_user.id)
        .order_by(models.UpgradeRefund.created_at.desc())
    )
    refunds = result.scalars().all()

    now = _utc_now()
    return [
        {
            "id": r.id,
            "amount": r.amount,
            "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "release_at": r.release_at.isoformat() if r.release_at else None,
            "released_at": r.released_at.isoformat() if r.released_at else None,
            # Remaining lock time in seconds (0 if already due/released)
            "seconds_until_release": max(
                0,
                int((r.release_at - now).total_seconds()) if r.release_at and r.status == "pending" else 0
            ),
        }
        for r in refunds
    ]
