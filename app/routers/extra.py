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
    passed_training: int

class ReferralCodeSchema(BaseModel):
    code: str
    signups: int
    trained: int
    earned: float

    class Config:
        orm_mode = True

class ReferralCodeCreate(BaseModel):
    code: str

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
    total_trained = sum(c.trained_count for c in codes)
    
    return {
        "earnings": total_earnings,
        "users_referred": total_signups,
        "passed_training": total_trained
    }

@router.get("/referrals/codes", response_model=List[ReferralCodeSchema])
async def get_referral_codes(
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    result = await db.execute(
        select(models.ReferralCode).filter(models.ReferralCode.user_id == current_user.id)
    )
    codes = result.scalars().all()
    return [{"code": c.code, "signups": c.signups_count, "trained": c.trained_count, "earned": c.earned_amount} for c in codes]

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
