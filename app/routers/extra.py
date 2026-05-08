from typing import List, Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.routers.auth import get_current_user
from app.models import models

router = APIRouter()

# --- Tasks ---
class Task(BaseModel):
    id: int
    name: str
    status: str

@router.get("/tasks", response_model=List[Task])
async def get_tasks(current_user: models.User = Depends(get_current_user)):
    return [{"id": 1, "name": "Atomic Action Labels", "status": "locked"}]

# --- Referrals ---
class ReferralSummary(BaseModel):
    earnings: float
    users_referred: int
    passed_training: int

class ReferralCode(BaseModel):
    code: str
    signups: int
    trained: int
    earned: float

@router.get("/referrals/summary", response_model=ReferralSummary)
async def get_referral_summary(current_user: models.User = Depends(get_current_user)):
    return {"earnings": 0.00, "users_referred": 0, "passed_training": 0}

@router.get("/referrals/codes", response_model=List[ReferralCode])
async def get_referral_codes(current_user: models.User = Depends(get_current_user)):
    return [{"code": "135I128E", "signups": 0, "trained": 0, "earned": 0.00}]

# --- Payments ---
class PaymentOverview(BaseModel):
    total_paid: float
    previous_unpaid: float
    current_pending: float

class PaymentHistory(BaseModel):
    period: str
    amount: float
    status: str

@router.get("/payments/overview", response_model=PaymentOverview)
async def get_payment_overview(current_user: models.User = Depends(get_current_user)):
    return {"total_paid": 0.00, "previous_unpaid": 0.00, "current_pending": 0.00}

@router.get("/payments/history", response_model=List[PaymentHistory])
async def get_payment_history(current_user: models.User = Depends(get_current_user)):
    return [{"period": "May 1-15, 2026", "amount": 0.00, "status": "in progress"}]

# --- Feedback ---
class Evaluation(BaseModel):
    id: int
    name: str
    episodes_completed: str
    episodes_passing_audit: str

@router.get("/feedback/evaluations", response_model=List[Evaluation])
async def get_evaluations(current_user: models.User = Depends(get_current_user)):
    return [{"id": 1, "name": "Tier 1 Evaluation 1", "episodes_completed": "0/5", "episodes_passing_audit": "0/0"}]

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
