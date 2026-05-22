from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from app.database.database import get_async_db
from app.routers.auth import get_current_user
from app.models import models

router = APIRouter()

# --- Tasks ---
class TaskSchema(BaseModel):
    id: int
    name: str
    status: str

    class Config:
        orm_mode = True

@router.get("/tasks", response_model=List[TaskSchema])
async def get_tasks(
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    result = await db.execute(select(models.Task))
    tasks = result.scalars().all()
    return tasks

# --- Referrals ---
class ReferralSummary(BaseModel):
    earnings: float
    users_referred: int
    task_rebate: float

class ReferralCodeSchema(BaseModel):
    code: str
    signups: int
    trained: int
    earned: float
    task_rebate: float

    class Config:
        orm_mode = True

class ReferralCodeCreate(BaseModel):
    code: str

class InvitedUser(BaseModel):
    name: str
    status: str
    tier: str

@router.get("/referrals/summary", response_model=ReferralSummary)
async def get_referral_summary(
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    result = await db.execute(
        select(models.ReferralCode).filter(models.ReferralCode.user_id == current_user.id)
    )
    codes = result.scalars().all()
    
    total_earnings = sum(c.earned_amount for c in codes)
    total_signups = sum(c.signups_count for c in codes)
    total_task_rebate = sum(getattr(c, "task_rebate_amount", 0.0) or 0.0 for c in codes)
    
    return {
        "earnings": total_earnings,
        "users_referred": total_signups,
        "task_rebate": total_task_rebate
    }

@router.get("/referrals/active", response_model=List[InvitedUser])
async def get_active_referrals(
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    invited_users = []
    
    # Helper to get status for a user
    async def get_user_status(user_id):
        task_result = await db.execute(
            select(models.UserVideoTask).filter(
                models.UserVideoTask.user_id == user_id,
                models.UserVideoTask.status == "completed"
            )
        )
        return "Completed" if task_result.scalars().first() else "Awaiting Task"

    # Tier A: Direct referrals (users referred by current_user)
    result_a = await db.execute(
        select(models.User)
        .join(models.ReferralRelationship, models.User.id == models.ReferralRelationship.user_id)
        .filter(models.ReferralRelationship.referrer_id == current_user.id)
    )
    tier_a_users = result_a.scalars().all()
    
    for u in tier_a_users:
        status = await get_user_status(u.id)
        invited_users.append({"name": f"{u.first_name or 'User'} {u.last_name or ''}".strip(), "status": status, "tier": "A"})
        
        # Tier B: Referrals of Tier A
        result_b = await db.execute(
            select(models.User)
            .join(models.ReferralRelationship, models.User.id == models.ReferralRelationship.user_id)
            .filter(models.ReferralRelationship.referrer_id == u.id)
        )
        tier_b_users = result_b.scalars().all()
        for ub in tier_b_users:
            status_b = await get_user_status(ub.id)
            invited_users.append({"name": f"{ub.first_name or 'User'} {ub.last_name or ''}".strip(), "status": status_b, "tier": "B"})
            
            # Tier C: Referrals of Tier B
            result_c = await db.execute(
                select(models.User)
                .join(models.ReferralRelationship, models.User.id == models.ReferralRelationship.user_id)
                .filter(models.ReferralRelationship.referrer_id == ub.id)
            )
            tier_c_users = result_c.scalars().all()
            for uc in tier_c_users:
                status_c = await get_user_status(uc.id)
                invited_users.append({"name": f"{uc.first_name or 'User'} {uc.last_name or ''}".strip(), "status": status_c, "tier": "C"})
        
    return invited_users

@router.get("/referrals/codes", response_model=List[ReferralCodeSchema])
async def get_referral_codes(
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    result = await db.execute(
        select(models.ReferralCode).filter(models.ReferralCode.user_id == current_user.id)
    )
    codes = result.scalars().all()
    return [{
        "code": c.code, 
        "signups": c.signups_count, 
        "trained": c.trained_count, 
        "earned": c.earned_amount,
        "task_rebate": getattr(c, "task_rebate_amount", 0.0) or 0.0
    } for c in codes]

@router.post("/referrals/codes", response_model=ReferralCodeSchema)
async def create_referral_code(
    code_data: ReferralCodeCreate,
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    # Check if code already exists
    existing_result = await db.execute(select(models.ReferralCode).filter(models.ReferralCode.code == code_data.code))
    if existing_result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Referral code already exists")
    
    new_code = models.ReferralCode(user_id=current_user.id, code=code_data.code)
    db.add(new_code)
    await db.commit()
    await db.refresh(new_code)
    
    return {"code": new_code.code, "signups": 0, "trained": 0, "earned": 0.0}

# --- Payments ---
class PaymentOverview(BaseModel):
    total_paid: float
    previous_unpaid: float
    current_pending: float

class PaymentHistorySchema(BaseModel):
    period: str
    amount: float
    status: str

    class Config:
        orm_mode = True

class PaymentMethodUpdate(BaseModel):
    type: str # crypto, wise
    details: dict

@router.get("/payments/overview", response_model=PaymentOverview)
async def get_payment_overview(
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    result = await db.execute(
        select(models.Payment).filter(models.Payment.user_id == current_user.id)
    )
    payments = result.scalars().all()
    
    total_paid = sum(p.amount for p in payments if p.status == "paid")
    pending = sum(p.amount for p in payments if p.status == "pending")
    
    return {
        "total_paid": total_paid,
        "previous_unpaid": 0.0,
        "current_pending": pending
    }

@router.get("/payments/history", response_model=List[PaymentHistorySchema])
async def get_payment_history(
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    result = await db.execute(
        select(models.Payment).filter(models.Payment.user_id == current_user.id)
    )
    payments = result.scalars().all()
    return [{"period": p.period, "amount": p.amount, "status": p.status} for p in payments]

@router.post("/payments/method", response_model=dict)
async def update_payment_method(
    method_data: PaymentMethodUpdate,
    current_user: models.User = Depends(get_current_user)
):
    # In a real app, you'd save this to a PaymentMethod table
    return {"message": "Payment method updated successfully"}

# --- Feedback ---
class EvaluationSchema(BaseModel):
    id: int
    name: str
    episodes_completed: str
    episodes_passing_audit: str

@router.get("/feedback/evaluations", response_model=List[EvaluationSchema])
async def get_evaluations(
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    result = await db.execute(
        select(models.Evaluation).filter(models.Evaluation.user_id == current_user.id)
    )
    evals = result.scalars().all()
    return [
        {
            "id": e.id,
            "name": e.name,
            "episodes_completed": f"{e.episodes_completed}/{e.total_episodes_required}",
            "episodes_passing_audit": f"{e.episodes_passing_audit}/0"
        } for e in evals
    ]

# --- Investment Plans ---
class PlanPurchase(BaseModel):
    plan_id: int

@router.get("/plans", response_model=List[dict])
async def get_plans(db: AsyncSession = Depends(get_async_db)):
    result = await db.execute(select(models.InvestmentPlan).filter(models.InvestmentPlan.is_active == True))
    plans = result.scalars().all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "price": p.price,
            "daily_tasks_limit": p.daily_tasks_limit,
            "validity_days": p.validity_days,
            "description": p.description
        } for p in plans
    ]

@router.post("/plans/purchase", response_model=dict)
async def purchase_plan(
    purchase: PlanPurchase,
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    # 1. Fetch the plan
    plan_result = await db.execute(select(models.InvestmentPlan).filter(models.InvestmentPlan.id == purchase.plan_id))
    plan = plan_result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
        
    # 2. Check if user has enough balance in deposit wallet
    current_deposit = getattr(current_user, "deposit_wallet_balance", 0.0) or 0.0
    if current_deposit < plan.price:
        raise HTTPException(status_code=400, detail="Insufficient deposit wallet balance")
        
    # 3. Deduct from deposit wallet
    setattr(current_user, "deposit_wallet_balance", current_deposit - plan.price)
    
    # 4. Create UserPlan record
    new_user_plan = models.UserPlan(
        user_id=current_user.id,
        plan_id=plan.id,
        purchase_price=plan.price,
        expires_at=datetime.now(timezone.utc) + timedelta(days=plan.validity_days)
    )
    db.add(new_user_plan)
    
    # 5. --- Multi-Tier Referral Commission (10% / 4% / 1%) ---
    commission_config = [("A", 0.10), ("B", 0.04), ("C", 0.01)]
    
    # Fetch initial referrer
    rel_result = await db.execute(select(models.ReferralRelationship).filter(models.ReferralRelationship.user_id == current_user.id))
    rel = rel_result.scalar_one_or_none()
    current_referrer_id = rel.referrer_id if rel else None
    
    for tier, percentage in commission_config:
        if not current_referrer_id:
            break
            
        referrer_result = await db.execute(select(models.User).filter(models.User.id == current_referrer_id))
        referrer = referrer_result.scalar_one_or_none()
        
        if referrer:
            commission_amount = plan.price * percentage
            
            # Credit to referrer's withdrawal wallet
            if hasattr(referrer, "withdrawal_wallet_balance"):
                ref_balance = getattr(referrer, "withdrawal_wallet_balance", 0.0) or 0.0
                setattr(referrer, "withdrawal_wallet_balance", ref_balance + commission_amount)
            
            # Update referral code stats
            code_result = await db.execute(select(models.ReferralCode).filter(models.ReferralCode.user_id == referrer.id).limit(1))
            ref_code = code_result.scalar_one_or_none()
            if ref_code:
                current_earned = getattr(ref_code, "earned_amount", 0.0) or 0.0
                setattr(ref_code, "earned_amount", current_earned + commission_amount)
            
            # Move up the chain using the relationship table
            next_rel_result = await db.execute(select(models.ReferralRelationship).filter(models.ReferralRelationship.user_id == referrer.id))
            next_rel = next_rel_result.scalar_one_or_none()
            current_referrer_id = next_rel.referrer_id if next_rel else None
        else:
            break
            
    await db.commit()
    return {"message": f"Successfully purchased {plan.name}. Commissions distributed."}

# --- Settings ---
class UserProfile(BaseModel):
    first_name: Optional[str]
    last_name: Optional[str]
    email: str

class UserProfileUpdate(BaseModel):
    first_name: Optional[str]
    last_name: Optional[str]

@router.get("/settings/profile", response_model=UserProfile)
async def get_profile(current_user: models.User = Depends(get_current_user)):
    return {
        "first_name": current_user.first_name,
        "last_name": current_user.last_name,
        "email": current_user.email
    }

@router.put("/settings/profile", response_model=UserProfile)
async def update_profile(
    profile_data: UserProfileUpdate,
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    if profile_data.first_name is not None:
        current_user.first_name = profile_data.first_name
    if profile_data.last_name is not None:
        current_user.last_name = profile_data.last_name
    
    await db.commit()
    await db.refresh(current_user)
    return current_user

@router.delete("/settings/account", response_model=dict)
async def delete_account(
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    await db.delete(current_user)
    await db.commit()
    return {"message": "Account deleted successfully"}
