from typing import List, Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
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

@router.get("/settings/profile", response_model=UserProfile)
async def get_profile(current_user: models.User = Depends(get_current_user)):
    return {
        "first_name": current_user.first_name,
        "last_name": current_user.last_name,
        "email": current_user.email
    }
