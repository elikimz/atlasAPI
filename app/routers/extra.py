from datetime import datetime, timedelta, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from app.database.database import get_async_db
from app.routers.auth import get_current_user
from app.models import models
import cloudinary
import cloudinary.uploader
from app.config import settings

# Configure Cloudinary
cloudinary.config(
    cloud_name=settings.CLOUDINARY_CLOUD_NAME,
    api_key=settings.CLOUDINARY_API_KEY,
    api_secret=settings.CLOUDINARY_API_SECRET,
)

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
    is_active: bool

@router.get("/referrals/summary", response_model=ReferralSummary)
async def get_referral_summary(
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    try:
        result = await db.execute(
            select(models.ReferralCode).filter(models.ReferralCode.user_id == current_user.id)
        )
        codes = result.scalars().all()
        
        total_earnings = sum(getattr(c, "earned_amount", 0.0) or 0.0 for c in codes)
        total_signups = sum(getattr(c, "signups_count", 0) or 0 for c in codes)
        total_task_rebate = sum(getattr(c, "task_rebate_amount", 0.0) or 0.0 for c in codes)
        
        return {
            "earnings": total_earnings,
            "users_referred": total_signups,
            "task_rebate": total_task_rebate
        }
    except Exception as e:
        print(f"Safe Referral Summary Error: {e}")
        return {"earnings": 0.0, "users_referred": 0, "task_rebate": 0.0}

@router.get("/referrals/active", response_model=List[InvitedUser])
async def get_active_referrals(
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    invited_users = []
    
    try:
        # Helper to get status for a user
        async def get_user_status_and_activity(user_id):
            # Check if user has an active plan
            plan_result = await db.execute(
                select(models.UserPlanHistory).filter(
                    models.UserPlanHistory.user_id == user_id,
                    models.UserPlanHistory.status == "active",
                    models.UserPlanHistory.expires_at > datetime.now(timezone.utc)
                )
            )
            has_active_plan = plan_result.scalars().first() is not None
            
            task_result = await db.execute(
                select(models.UserVideoTask).filter(
                    models.UserVideoTask.user_id == user_id,
                    models.UserVideoTask.status == "completed"
                )
            )
            status = "Accepted" if task_result.scalars().first() else "Invite Sent"
            return status, has_active_plan

        # Tier A: Direct referrals (users referred by current_user)
        result_a = await db.execute(
            select(models.User)
            .join(models.ReferralRelationship, models.User.id == models.ReferralRelationship.user_id)
            .filter(models.ReferralRelationship.referrer_id == current_user.id)
        )
        tier_a_users = result_a.scalars().all()
        
        for u in tier_a_users:
            status, is_active = await get_user_status_and_activity(u.id)
            invited_users.append({
                "name": f"{u.first_name or 'User'} {u.last_name or ''}".strip(), 
                "status": status, 
                "tier": "A",
                "is_active": is_active
            })
            
            # Tier B: Referrals of Tier A
            result_b = await db.execute(
                select(models.User)
                .join(models.ReferralRelationship, models.User.id == models.ReferralRelationship.user_id)
                .filter(models.ReferralRelationship.referrer_id == u.id)
            )
            tier_b_users = result_b.scalars().all()
            for ub in tier_b_users:
                status_b, is_active_b = await get_user_status_and_activity(ub.id)
                invited_users.append({
                    "name": f"{ub.first_name or 'User'} {ub.last_name or ''}".strip(), 
                    "status": status_b, 
                    "tier": "B",
                    "is_active": is_active_b
                })
                
                # Tier C: Referrals of Tier B
                result_c = await db.execute(
                    select(models.User)
                    .join(models.ReferralRelationship, models.User.id == models.ReferralRelationship.user_id)
                    .filter(models.ReferralRelationship.referrer_id == ub.id)
                )
                tier_c_users = result_c.scalars().all()
                for uc_user in tier_c_users:
                    status_c, is_active_c = await get_user_status_and_activity(uc_user.id)
                    invited_users.append({
                        "name": f"{uc_user.first_name or 'User'} {uc_user.last_name or ''}".strip(), 
                        "status": status_c, 
                        "tier": "C",
                        "is_active": is_active_c
                    })
    except Exception as e:
        print(f"Safe Active Referrals Error: {e}")
        
    return invited_users

@router.get("/referrals/codes", response_model=List[ReferralCodeSchema])
async def get_referral_codes(
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    try:
        result = await db.execute(
            select(models.ReferralCode).filter(models.ReferralCode.user_id == current_user.id)
        )
        codes = result.scalars().all()
        
        # If no code record exists but user has a referral_code in the users table, create it
        if not codes and current_user.referral_code:
            new_code = models.ReferralCode(user_id=current_user.id, code=current_user.referral_code)
            db.add(new_code)
            await db.commit()
            await db.refresh(new_code)
            codes = [new_code]
            
        return [{
            "code": c.code, 
            "signups": getattr(c, "signups_count", 0) or 0, 
            "trained": getattr(c, "trained_count", 0) or 0, 
            "earned": getattr(c, "earned_amount", 0.0) or 0.0,
            "task_rebate": getattr(c, "task_rebate_amount", 0.0) or 0.0
        } for c in codes]
    except Exception as e:
        print(f"Safe Referral Codes Error: {e}")
        return []

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

@router.post("/payments/deposit")
async def create_deposit_request(
    amount: float,
    payment_method: str,
    network: str,
    proof_url: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: models.User = Depends(get_current_user)
):
    try:
        new_payment = models.Payment(
            user_id=current_user.id,
            amount=amount,
            period=datetime.now(timezone.utc).strftime("%b %Y"),
            status="pending",
            type="deposit",
            payment_method=payment_method,
            network=network,
            proof_url=proof_url
        )
        db.add(new_payment)
        await db.commit()
        await db.refresh(new_payment)
        return new_payment
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/payments/upload-proof")
async def upload_payment_proof(
    file: UploadFile = File(...),
    current_user: models.User = Depends(get_current_user)
):
    try:
        # Note: Added folder "payment_proofs" and using synchronous upload 
        # as seen in other parts of the app
        import os
        c_name = settings.CLOUDINARY_CLOUD_NAME or os.getenv("CLOUDINARY_CLOUD_NAME")
        c_key = settings.CLOUDINARY_API_KEY or os.getenv("CLOUDINARY_API_KEY")
        c_secret = settings.CLOUDINARY_API_SECRET or os.getenv("CLOUDINARY_API_SECRET")

        upload_result = cloudinary.uploader.upload(
            file.file, 
            folder="payment_proofs",
            resource_type="auto",
            cloud_name=c_name,
            api_key=c_key,
            api_secret=c_secret
        )
        return {"url": upload_result.get("secure_url")}
    except Exception as e:
        print(f"Upload error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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

# --- Investment Plans (Handled by plans.py router) ---

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
