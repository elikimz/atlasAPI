from datetime import datetime, timedelta, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from app.database.database import get_async_db
from app.routers.auth import get_current_user
from app.models import models
from app.config import settings

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
    id: int
    period: str
    amount: float
    status: str
    type: str
    payment_method: Optional[str]
    network: Optional[str]
    proof_url: Optional[str]
    admin_notes: Optional[str]
    created_at: Optional[str]

    class Config:
        orm_mode = True

class PaymentMethodUpdate(BaseModel):
    type: str # crypto, wise
    details: dict

class DepositRequestSchema(BaseModel):
    amount: float
    payment_method: str
    network: str
    proof_url: str

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
        select(models.Payment).filter(models.Payment.user_id == current_user.id).order_by(models.Payment.created_at.desc())
    )
    payments = result.scalars().all()
    return [{
        "id": p.id,
        "period": p.period,
        "amount": p.amount,
        "status": p.status,
        "type": p.type,
        "payment_method": p.payment_method,
        "network": p.network,
        "proof_url": p.proof_url,
        "admin_notes": p.admin_notes,
        "created_at": p.created_at.isoformat() if p.created_at else None
    } for p in payments]

@router.post("/payments/method", response_model=dict)
async def update_payment_method(
    method_data: PaymentMethodUpdate,
    current_user: models.User = Depends(get_current_user)
):
    # In a real app, you'd save this to a PaymentMethod table
    return {"message": "Payment method updated successfully"}

@router.post("/payments/deposit", response_model=dict)
async def create_deposit_request(
    deposit_data: DepositRequestSchema,
    db: AsyncSession = Depends(get_async_db),
    current_user: models.User = Depends(get_current_user)
):
    try:
        new_payment = models.Payment(
            user_id=current_user.id,
            amount=deposit_data.amount,
            period=datetime.now(timezone.utc).strftime("%b %Y"),
            status="pending",
            type="deposit",
            payment_method=deposit_data.payment_method,
            network=deposit_data.network,
            proof_url=deposit_data.proof_url
        )
        db.add(new_payment)
        await db.commit()
        await db.refresh(new_payment)
        return {
            "id": new_payment.id,
            "status": new_payment.status,
            "message": "Deposit request submitted successfully. Please wait for admin approval."
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

# Note: upload-proof endpoint removed. Uploads are now handled directly by the frontend using an unsigned preset.

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

# --- Withdrawal Accounts ---
class WithdrawalAccountSchema(BaseModel):
    id: int
    type: str
    label: Optional[str]
    address: str
    network: Optional[str]
    is_verified: bool
    is_primary: bool

    class Config:
        orm_mode = True

class WithdrawalAccountCreate(BaseModel):
    type: str
    label: Optional[str]
    address: str
    network: Optional[str]
    is_primary: bool = False

@router.get("/withdrawal-accounts", response_model=List[WithdrawalAccountSchema])
async def get_withdrawal_accounts(
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    result = await db.execute(
        select(models.WithdrawalAccount).filter(models.WithdrawalAccount.user_id == current_user.id)
    )
    return result.scalars().all()

@router.post("/withdrawal-accounts", response_model=WithdrawalAccountSchema)
async def add_withdrawal_account(
    account_data: WithdrawalAccountCreate,
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    # If this is primary, unset other primary accounts
    if account_data.is_primary:
        await db.execute(
            models.WithdrawalAccount.__table__.update()
            .where(models.WithdrawalAccount.user_id == current_user.id)
            .values(is_primary=False)
        )
    
    new_account = models.WithdrawalAccount(
        user_id=current_user.id,
        **account_data.dict()
    )
    db.add(new_account)
    await db.commit()
    await db.refresh(new_account)
    return new_account

# --- Withdrawal Workflow ---
class WithdrawalRequest(BaseModel):
    amount: float
    account_id: int
    password: str

class WithdrawalPasswordSet(BaseModel):
    password: str

@router.post("/settings/withdrawal-password", response_model=dict)
async def set_withdrawal_password(
    data: WithdrawalPasswordSet,
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    # For simplicity, we'll store it directly for now, but in production, use hashing
    current_user.withdrawal_password = data.password
    await db.commit()
    return {"message": "Withdrawal password set successfully"}

@router.post("/payments/withdraw", response_model=dict)
async def request_withdrawal(
    withdrawal_data: WithdrawalRequest,
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    # 1. Verify withdrawal password
    if not current_user.withdrawal_password or current_user.withdrawal_password != withdrawal_data.password:
        raise HTTPException(status_code=403, detail="Invalid withdrawal password")
    
    # 2. Check balance
    if current_user.withdrawal_wallet_balance < withdrawal_data.amount:
        raise HTTPException(status_code=400, detail="Insufficient withdrawal balance")
    
    # 3. Get account details
    result = await db.execute(
        select(models.WithdrawalAccount).filter(
            models.WithdrawalAccount.id == withdrawal_data.account_id,
            models.WithdrawalAccount.user_id == current_user.id
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Withdrawal account not found")
    
    # 4. Create payment record
    try:
        from datetime import datetime, timezone
        new_payment = models.Payment(
            user_id=current_user.id,
            amount=withdrawal_data.amount,
            period=datetime.now(timezone.utc).strftime("%b %Y"),
            status="pending",
            type="payout",
            payment_method=f"{account.type.upper()} ({account.network})",
            network=account.network,
            admin_notes=f"Withdrawal to {account.address}"
        )
        
        # Deduct balance
        current_user.withdrawal_wallet_balance -= withdrawal_data.amount
        
        db.add(new_payment)
        await db.commit()
        
        return {
            "message": "Withdrawal submitted successfully. Your funds are being processed.",
            "payment_id": new_payment.id
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

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
