from datetime import datetime, timedelta, timezone
from hmac import compare_digest
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status, UploadFile, File
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func
from app.database.database import get_async_db
from app.routers.auth import get_current_admin_user, get_current_user, get_password_hash, verify_password
from app.models import models
from app.config import settings
from app.services.cache import CacheKeys, CacheTTL, cache, invalidate_shared_cache, invalidate_user_cache

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
    total_invites: int
    active_invites: int
    tier_a_invite_earnings: float
    tier_b_invite_earnings: float
    tier_c_invite_earnings: float
    tier_a_task_rebate: float
    tier_b_task_rebate: float
    tier_c_task_rebate: float

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
    db: AsyncSession = Depends(get_async_db),
):
    """Serve a user-scoped referral summary from the five-minute cache."""
    async def load_referral_summary() -> dict:
        try:
            result = await db.execute(
                select(models.ReferralCode).filter(models.ReferralCode.user_id == current_user.id)
            )
            codes = result.scalars().all()
            total_invites_result = await db.execute(
                select(func.count(models.User.id))
                .join(models.ReferralRelationship, models.User.id == models.ReferralRelationship.user_id)
                .filter(models.ReferralRelationship.referrer_id == current_user.id)
            )
            active_invites_result = await db.execute(
                select(func.count(models.User.id))
                .join(models.ReferralRelationship, models.User.id == models.ReferralRelationship.user_id)
                .filter(
                    models.ReferralRelationship.referrer_id == current_user.id,
                    models.User.current_plan_id.is_not(None),
                )
            )
            return {
                "earnings": sum((code.earned_amount or 0.0) for code in codes),
                "users_referred": sum((code.signups_count or 0) for code in codes),
                "task_rebate": sum((code.task_rebate_amount or 0.0) for code in codes),
                "total_invites": total_invites_result.scalar() or 0,
                "active_invites": active_invites_result.scalar() or 0,
                "tier_a_invite_earnings": sum((code.tier_a_invite_earnings or 0.0) for code in codes),
                "tier_b_invite_earnings": sum((code.tier_b_invite_earnings or 0.0) for code in codes),
                "tier_c_invite_earnings": sum((code.tier_c_invite_earnings or 0.0) for code in codes),
                "tier_a_task_rebate": sum((code.tier_a_task_rebate or 0.0) for code in codes),
                "tier_b_task_rebate": sum((code.tier_b_task_rebate or 0.0) for code in codes),
                "tier_c_task_rebate": sum((code.tier_c_task_rebate or 0.0) for code in codes),
            }
        except Exception as exc:
            print(f"Safe Referral Summary Error: {exc}")
            return {
                "earnings": 0.0,
                "users_referred": 0,
                "task_rebate": 0.0,
                "total_invites": 0,
                "active_invites": 0,
                "tier_a_invite_earnings": 0.0,
                "tier_b_invite_earnings": 0.0,
                "tier_c_invite_earnings": 0.0,
                "tier_a_task_rebate": 0.0,
                "tier_b_task_rebate": 0.0,
                "tier_c_task_rebate": 0.0,
            }

    return await cache.get_or_set(
        CacheKeys.user_referral_summary(current_user.id),
        CacheTTL.REFERRALS,
        load_referral_summary,
    )

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
    db: AsyncSession = Depends(get_async_db),
):
    async def load_referral_codes() -> list[dict]:
        try:
            result = await db.execute(
                select(models.ReferralCode).filter(models.ReferralCode.user_id == current_user.id)
            )
            codes = result.scalars().all()

            # Maintain the legacy lazily-created code behavior, then cache the
            # resulting representation only after the creation commit succeeds.
            if not codes and current_user.referral_code:
                new_code = models.ReferralCode(user_id=current_user.id, code=current_user.referral_code)
                db.add(new_code)
                await db.commit()
                await db.refresh(new_code)
                codes = [new_code]

            return [
                {
                    "code": code.code,
                    "signups": getattr(code, "signups_count", 0) or 0,
                    "trained": getattr(code, "trained_count", 0) or 0,
                    "earned": getattr(code, "earned_amount", 0.0) or 0.0,
                    "task_rebate": getattr(code, "task_rebate_amount", 0.0) or 0.0,
                }
                for code in codes
            ]
        except Exception as exc:
            print(f"Safe Referral Codes Error: {exc}")
            return []

    return await cache.get_or_set(
        CacheKeys.user_referral_codes(current_user.id),
        CacheTTL.REFERRALS,
        load_referral_codes,
    )

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
    await invalidate_user_cache(current_user.id, "referrals", "dashboard")

    return {
        "code": new_code.code,
        "signups": 0,
        "trained": 0,
        "earned": 0.0,
        "task_rebate": 0.0
    }

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
    db: AsyncSession = Depends(get_async_db),
):
    async def load_payment_overview() -> dict:
        result = await db.execute(
            select(models.Payment).filter(models.Payment.user_id == current_user.id)
        )
        payments = result.scalars().all()
        return {
            "total_paid": sum(payment.amount for payment in payments if payment.status == "paid"),
            "previous_unpaid": 0.0,
            "current_pending": sum(payment.amount for payment in payments if payment.status == "pending"),
        }

    return await cache.get_or_set(
        CacheKeys.user_payment_overview(current_user.id),
        CacheTTL.PAYMENTS,
        load_payment_overview,
    )


@router.get("/payments/history", response_model=List[PaymentHistorySchema])
async def get_payment_history(
    response: Response,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Return a bounded history page without breaking existing array consumers.

    Pagination metadata is exposed as response headers so legacy frontend code
    continues to consume an array while new clients can build page controls.
    """
    async def load_payment_page() -> dict:
        total_result = await db.execute(
            select(func.count(models.Payment.id)).filter(models.Payment.user_id == current_user.id)
        )
        total = total_result.scalar() or 0
        result = await db.execute(
            select(models.Payment)
            .filter(models.Payment.user_id == current_user.id)
            .order_by(models.Payment.created_at.desc(), models.Payment.id.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
        return {
            "items": [
                {
                    "id": payment.id,
                    "period": payment.period,
                    "amount": payment.amount,
                    "status": payment.status,
                    "type": payment.type,
                    "payment_method": payment.payment_method,
                    "network": payment.network,
                    "proof_url": payment.proof_url,
                    "admin_notes": payment.admin_notes,
                    "created_at": payment.created_at.isoformat() if payment.created_at else None,
                }
                for payment in result.scalars().all()
            ],
            "total": total,
        }

    page_data = await cache.get_or_set(
        CacheKeys.user_payments(current_user.id, page, limit),
        CacheTTL.PAYMENTS,
        load_payment_page,
    )
    response.headers["X-Total-Count"] = str(page_data["total"])
    response.headers["X-Page"] = str(page)
    response.headers["X-Limit"] = str(limit)
    response.headers["X-Has-More"] = str(page * limit < page_data["total"]).lower()
    return page_data["items"]

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
        await invalidate_user_cache(current_user.id, "payments", "dashboard")
        await invalidate_shared_cache("admin_stats")
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
    full_name: Optional[str] = None

    class Config:
        orm_mode = True

class WithdrawalAccountCreate(BaseModel):
    type: str
    label: Optional[str]
    address: str
    network: Optional[str]
    is_primary: bool = False
    full_name: Optional[str] = None

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
    await invalidate_user_cache(current_user.id, "all")
    return new_account

# --- Withdrawal Workflow ---
class WithdrawalRequest(BaseModel):
    amount: float = Field(gt=0)
    account_id: int = Field(gt=0)
    password: str = Field(min_length=1, max_length=72)


class WithdrawalPasswordUpdate(BaseModel):
    current_password: Optional[str] = Field(default=None, min_length=1, max_length=72)
    new_password: str = Field(min_length=4, max_length=72)


def _verify_withdrawal_password(plain_password: str, stored_password: str) -> bool:
    """Verify hashed values and support a one-time migration from legacy plaintext."""
    if stored_password.startswith(("$2a$", "$2b$", "$2y$")):
        return verify_password(plain_password, stored_password)
    return compare_digest(stored_password, plain_password)

@router.post("/settings/withdrawal-password", response_model=dict)
async def update_withdrawal_password(
    data: WithdrawalPasswordUpdate,
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    # If a password already exists, require the current value before replacement.
    if current_user.withdrawal_password:
        if not data.current_password:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current withdrawal password is required to set a new one.")
        if not _verify_withdrawal_password(data.current_password, current_user.withdrawal_password):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Incorrect current withdrawal password.")

    current_user.withdrawal_password = get_password_hash(data.new_password)
    await db.commit()
    await invalidate_user_cache(current_user.id, "all")
    return {"message": "Withdrawal password updated successfully"}

@router.post("/payments/withdraw", response_model=dict)
async def request_withdrawal(
    withdrawal_data: WithdrawalRequest,
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    # Withdrawal eligibility requires a completed recharge-funded plan purchase.
    # This is enforced server-side so it cannot be bypassed by changing the UI.
    if not current_user.has_purchased_first_package or not current_user.current_plan_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Recharge your account and purchase a plan before requesting a withdrawal.",
        )

    # 1. Verify the withdrawal password. Successful legacy plaintext checks are
    # immediately upgraded to bcrypt so users are not locked out during rollout.
    if not current_user.withdrawal_password or not _verify_withdrawal_password(withdrawal_data.password, current_user.withdrawal_password):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid withdrawal password")
    if not current_user.withdrawal_password.startswith(("$2a$", "$2b$", "$2y$")):
        current_user.withdrawal_password = get_password_hash(withdrawal_data.password)

    # 2. Check balance
    if (current_user.withdrawal_wallet_balance or 0.0) < withdrawal_data.amount:
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
            admin_notes=f"Withdrawal to {account.address}",
            destination_number=account.address
        )
        
        # Deduct balance
        current_user.withdrawal_wallet_balance = (current_user.withdrawal_wallet_balance or 0.0) - withdrawal_data.amount
        
        db.add(new_payment)
        await db.commit()
        await invalidate_user_cache(current_user.id, "payments", "dashboard")
        await invalidate_shared_cache("admin_stats")

        return {
            "message": "Withdrawal submitted successfully. Your funds are being processed.",
            "payment_id": new_payment.id
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

# --- Settings ---
class UserProfile(BaseModel):
    username: str
    first_name: Optional[str]
    last_name: Optional[str]
    email: Optional[str]
    phone_number: Optional[str]
    has_withdrawal_password: bool

class UserProfileUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None

class AppConfigSchema(BaseModel):
    key: str
    value: str

@router.get("/settings/profile", response_model=UserProfile)
async def get_profile(current_user: models.User = Depends(get_current_user)):
    return {
        "username": current_user.username,
        "first_name": current_user.first_name,
        "last_name": current_user.last_name,
        "email": current_user.email,
        "phone_number": current_user.phone_number,
        "has_withdrawal_password": bool(current_user.withdrawal_password)
    }

@router.get("/settings/config", response_model=List[AppConfigSchema])
async def get_app_config(db: AsyncSession = Depends(get_async_db)):
    async def load_app_config() -> list[dict]:
        result = await db.execute(select(models.AppConfig))
        config_map = {config.key: config.value for config in result.scalars().all()}
        default_keys = {
            "telegram_link": "https://t.me/AdPulseAI",
            "whatsapp_link": "https://chat.whatsapp.com/L1234567890",
            "support_ticket_url": "https://help.adpulseai.com",
        }
        return [
            {"key": key, "value": config_map.get(key, default_value)}
            for key, default_value in default_keys.items()
        ]

    return await cache.get_or_set(CacheKeys.app_config(), CacheTTL.APP_CONFIG, load_app_config)

@router.put("/admin/config", response_model=dict)
async def update_app_config(
    config_data: AppConfigSchema,
    current_user: models.User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_async_db)
):
    result = await db.execute(select(models.AppConfig).filter(models.AppConfig.key == config_data.key))
    config = result.scalar_one_or_none()
    
    if config:
        config.value = config_data.value
    else:
        config = models.AppConfig(key=config_data.key, value=config_data.value)
        db.add(config)
    
    await db.commit()
    await invalidate_shared_cache("app_config")
    return {"message": f"Configuration {config_data.key} updated successfully"}

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
    await invalidate_user_cache(current_user.id, "all")
    return {
        "username": current_user.username,
        "first_name": current_user.first_name,
        "last_name": current_user.last_name,
        "email": current_user.email,
        "phone_number": current_user.phone_number,
        "has_withdrawal_password": bool(current_user.withdrawal_password)
    }

@router.delete("/settings/account", response_model=dict)
async def delete_account(
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    await db.delete(current_user)
    await db.commit()
    await invalidate_user_cache(current_user.id, "all")
    await invalidate_shared_cache("admin_stats")
    return {"message": "Account deleted successfully"}
