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
    result = await db.execute(select(models.Plan).filter(models.Plan.id == plan_id))
    plan = result.scalar_one_or_none()
    if not plan or not plan.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")

    if _plan_is_active(current_user):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User already has an active plan. Please upgrade instead.")

    if _plan_is_expired(current_user):
        expired_result = await db.execute(select(models.Plan).filter(models.Plan.id == current_user.current_plan_id))
        expired_plan = expired_result.scalar_one_or_none()
        if expired_plan and plan.price <= expired_plan.price:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Your previous plan has expired. You must upgrade to a higher tier.")

    # The Intern/free-trial plan has a zero price and must activate without money.
    if plan.price > 0 and current_user.deposit_wallet_balance < plan.price:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Insufficient balance to purchase this plan")

    now = _utc_now()
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
    
    # --- Referral Commission Logic (First Purchase Only) ---
    if not current_user.has_purchased_first_package and plan.price > 0:
        current_user.has_purchased_first_package = True
        
        # 3-Tier Commission Split: Tier A (10%), Tier B (4%), Tier C (1%)
        commission_config = [("tier_a_invite_earnings", 0.10), ("tier_b_invite_earnings", 0.04), ("tier_c_invite_earnings", 0.01)]
        
        # Find upline referrers
        rel_result = await db.execute(select(models.ReferralRelationship).filter(models.ReferralRelationship.user_id == current_user.id))
        rel = rel_result.scalar_one_or_none()
        current_upline_id = rel.referrer_id if rel else None
        
        for field_name, percentage in commission_config:
            if not current_upline_id:
                break
                
            upline_result = await db.execute(select(models.User).filter(models.User.id == current_upline_id))
            upline = upline_result.scalar_one_or_none()
            
            if upline:
                commission_amount = plan.price * percentage
                # Add to upline's withdrawal balance
                upline.withdrawal_wallet_balance = (upline.withdrawal_wallet_balance or 0.0) + commission_amount
                
                # Update upline's referral code stats
                code_result = await db.execute(select(models.ReferralCode).filter(models.ReferralCode.user_id == upline.id).limit(1))
                ref_code = code_result.scalar_one_or_none()
                if ref_code:
                    current_val = getattr(ref_code, field_name, 0.0) or 0.0
                    setattr(ref_code, field_name, current_val + commission_amount)
                    # Also update legacy total
                    ref_code.earned_amount = (ref_code.earned_amount or 0.0) + commission_amount
                
                # Move up to next tier
                next_rel_result = await db.execute(select(models.ReferralRelationship).filter(models.ReferralRelationship.user_id == upline.id))
                next_rel = next_rel_result.scalar_one_or_none()
                current_upline_id = next_rel.referrer_id if next_rel else None
            else:
                break

    # Auto-assign tasks for the new plan
    result_tasks = await db.execute(select(models.VideoTask).filter(models.VideoTask.plan_id == plan.id))
    plan_tasks = result_tasks.scalars().all()
    for task in plan_tasks:
        # Check if user already has this task to avoid duplicates
        existing_task_result = await db.execute(
            select(models.UserVideoTask).filter(
                models.UserVideoTask.user_id == current_user.id,
                models.UserVideoTask.video_task_id == task.id
            )
        )
        if not existing_task_result.scalar_one_or_none():
            db.add(models.UserVideoTask(user_id=current_user.id, video_task_id=task.id, status="pending"))
            
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
    result = await db.execute(select(models.Plan).filter(models.Plan.id == new_plan_id))
    new_plan = result.scalar_one_or_none()
    if not new_plan or not new_plan.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="New plan not found")

    if not current_user.current_plan_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No existing plan to upgrade from. Please purchase a plan first.")

    result = await db.execute(select(models.Plan).filter(models.Plan.id == current_user.current_plan_id))
    current_plan = result.scalar_one_or_none()
    if not current_plan:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Current plan not found in database.")

    if new_plan.price <= current_plan.price:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New plan must be a higher tier than the current plan.")

    result = await db.execute(
        select(models.UserPlanHistory)
        .filter(
            models.UserPlanHistory.user_id == current_user.id,
            models.UserPlanHistory.plan_id == current_plan.id,
            models.UserPlanHistory.status == "active"
        )
        .order_by(models.UserPlanHistory.purchased_at.desc())
    )
    # Use .scalars().first() to avoid MultipleResultsFound error
    old_user_plan_entry = result.scalars().first()
    refund_amount = old_user_plan_entry.purchase_price if old_user_plan_entry else current_user.plan_purchase_price or 0.0

    # Upgrades require the net additional amount after the old plan refund.
    # This allows users to upgrade even if they don't have the full new plan price upfront.
    required_additional = max(new_plan.price - refund_amount, 0.0)
    
    if current_user.deposit_wallet_balance < required_additional:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient balance to upgrade. Required additional: ${required_additional:.2f}. Your previous plan price (${refund_amount:.2f}) is credited as a refund."
        )

    now = _utc_now()
    # Charge the net difference from the deposit wallet
    current_user.deposit_wallet_balance -= required_additional
    # Also record the refund in the performance bonus balance as requested
    current_user.performance_bonus_balance = (current_user.performance_bonus_balance or 0.0) + refund_amount
    
    # Ensure first purchase flag is set if they upgrade from a paid plan (though usually purchase comes first)
    if plan.price > 0:
        current_user.has_purchased_first_package = True

    if old_user_plan_entry:
        old_user_plan_entry.status = "upgraded"
        old_user_plan_entry.refunded_amount = refund_amount
        db.add(old_user_plan_entry)

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
    
    # --- Good Business Logic: Clean up old plan tasks ---
    # When upgrading, we remove "pending" tasks from the old plan that were never started/completed.
    # This ensures the user's task list stays clean and relevant to their new tier.
    await db.execute(
        models.UserVideoTask.__table__.delete().where(
            models.UserVideoTask.user_id == current_user.id,
            models.UserVideoTask.status == "pending"
        )
    )

    # Auto-assign tasks for the new upgraded plan
    result_tasks = await db.execute(select(models.VideoTask).filter(models.VideoTask.plan_id == new_plan.id))
    plan_tasks = result_tasks.scalars().all()
    for task in plan_tasks:
        # We don't need to check for existing here because we just cleared pending tasks,
        # and completed tasks should remain in history.
        db.add(models.UserVideoTask(user_id=current_user.id, video_task_id=task.id, status="pending"))
            
    await db.commit()

    await db.refresh(new_user_plan_history)
    await db.execute(
        select(models.User)
        .options(selectinload(models.User.current_plan))
        .filter(models.User.id == current_user.id)
    )

    return new_user_plan_history
