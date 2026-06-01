from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from datetime import datetime, timedelta

from app.database.database import get_async_db
from app.models import models
from app.schemas import plan as plan_schemas
from app.core.security import get_current_active_user

router = APIRouter(
    prefix="/plans",
    tags=["plans"]
)

@router.get("/", response_model=list[plan_schemas.Plan])
async def get_all_plans(db: AsyncSession = Depends(get_async_db)):
    result = await db.execute(select(models.Plan))
    plans = result.scalars().all()
    return plans

@router.post("/purchase/{plan_id}", response_model=plan_schemas.UserPlanHistory)
async def purchase_plan(
    plan_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: models.User = Depends(get_current_active_user)
):
    # Fetch the plan details
    result = await db.execute(select(models.Plan).filter(models.Plan.id == plan_id))
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")

    # Check if user already has an active plan
    if current_user.current_plan_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User already has an active plan. Please upgrade instead.")

    # Check if user has enough balance
    if current_user.deposit_wallet_balance < plan.price:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Insufficient balance to purchase this plan")

    # Deduct plan price from user's deposit wallet
    current_user.deposit_wallet_balance -= plan.price

    # Update user's current plan details
    current_user.current_plan_id = plan.id
    current_user.plan_start_date = datetime.utcnow()
    current_user.plan_expiry_date = datetime.utcnow() + timedelta(days=plan.validity_days)

    # Record plan purchase in history
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
    await db.commit()
    await db.refresh(current_user)
    await db.refresh(user_plan_history)

    return user_plan_history

@router.post("/upgrade/{new_plan_id}", response_model=plan_schemas.UserPlanHistory)
async def upgrade_plan(
    new_plan_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: models.User = Depends(get_current_active_user)
):
    # Fetch new plan details
    result = await db.execute(select(models.Plan).filter(models.Plan.id == new_plan_id))
    new_plan = result.scalar_one_or_none()
    if not new_plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="New plan not found")

    # Check if user has an active plan to upgrade from
    if not current_user.current_plan_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active plan to upgrade from. Please purchase a plan first.")

    # Fetch current active plan details
    result = await db.execute(select(models.Plan).filter(models.Plan.id == current_user.current_plan_id))
    current_active_plan = result.scalar_one_or_none()
    if not current_active_plan:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Current active plan not found in database.")

    # Check if new plan is actually an upgrade (higher price)
    if new_plan.price <= current_active_plan.price:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New plan must be a higher tier than the current active plan.")

    # Check if user has enough balance for the new plan (after potential refund)
    # The refund of the old plan's price happens *after* the new plan's price deduction
    # So, we check if current balance is enough for the new plan first.
    if current_user.deposit_wallet_balance < new_plan.price:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Insufficient balance to upgrade to this plan.")

    # Start transaction
    async with db.begin():
        # 1. Deduct new plan price from user's deposit wallet
        current_user.deposit_wallet_balance -= new_plan.price

        # 2. Refund the initial purchase price of the old plan
        # Find the active entry in UserPlanHistory for the current_active_plan
        result = await db.execute(
            select(models.UserPlanHistory)
            .filter(
                models.UserPlanHistory.user_id == current_user.id,
                models.UserPlanHistory.plan_id == current_active_plan.id,
                models.UserPlanHistory.status == "active"
            )
            .order_by(models.UserPlanHistory.purchased_at.desc())
        )
        old_user_plan_entry = result.scalar_one_or_none()

        refund_amount = 0.0
        if old_user_plan_entry:
            refund_amount = old_user_plan_entry.purchase_price
            current_user.deposit_wallet_balance += refund_amount
            old_user_plan_entry.status = "upgraded"
            old_user_plan_entry.refunded_amount = refund_amount
            db.add(old_user_plan_entry)

        # 3. Update user's current plan details to the new plan
        current_user.current_plan_id = new_plan.id
        current_user.plan_start_date = datetime.utcnow()
        current_user.plan_expiry_date = datetime.utcnow() + timedelta(days=new_plan.validity_days)

        # 4. Record new plan purchase in history
        new_user_plan_history = models.UserPlanHistory(
            user_id=current_user.id,
            plan_id=new_plan.id,
            purchase_price=new_plan.price,
            purchased_at=current_user.plan_start_date,
            expires_at=current_user.plan_expiry_date,
            status="active",
            refunded_amount=0.0 # New plan has no refund yet
        )
        db.add(new_user_plan_history)

        db.add(current_user)
        await db.commit()
        await db.refresh(current_user)
        await db.refresh(new_user_plan_history)

    return new_user_plan_history
