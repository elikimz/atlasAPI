from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text
from sqlalchemy.orm import selectinload
from app.database.database import get_async_db
from app.models.models import User, VideoTask, Certification, Payment, Plan, ReferralCode, ReferralRelationship
from app.routers.auth import get_current_admin_user
from pydantic import BaseModel
import cloudinary
import cloudinary.uploader
import os
from datetime import datetime
from app.config import settings
from app.services.cache import CacheKeys, CacheTTL, cache

# Configure Cloudinary
cloudinary.config(
    cloud_name=settings.CLOUDINARY_CLOUD_NAME,
    api_key=settings.CLOUDINARY_API_KEY,
    api_secret=settings.CLOUDINARY_API_SECRET,
)

# --- Schemas ---

class VideoTaskCreate(BaseModel):
    title: str
    description: str
    reward_amount: float
    video_url: str
    plan_id: int | None = None

class VideoTaskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    reward_amount: float | None = None
    video_url: str | None = None
    plan_id: int | None = None

class PlanCreate(BaseModel):
    name: str
    price: float
    daily_tasks_limit: int
    validity_days: int
    description: str
    is_active: bool = True
    is_upgrade_only: bool = False

class PlanUpdate(BaseModel):
    name: str | None = None
    price: float | None = None
    daily_tasks_limit: int | None = None
    validity_days: int | None = None
    description: str | None = None
    is_active: bool | None = None
    is_upgrade_only: bool | None = None

class CertificationCreate(BaseModel):
    name: str
    description: str | None = None
    estimated_time: str | None = None
    video_url: str | None = None
    steps_count: int = 0
    is_active: bool = True

class CertificationUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    estimated_time: str | None = None
    video_url: str | None = None
    steps_count: int | None = None
    is_active: bool | None = None

class UserUpdate(BaseModel):
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone_number: str | None = None
    role: str | None = None
    is_admin: bool | None = None
    is_suspended: bool | None = None
    is_trained: bool | None = None
    deposit_wallet_balance: float | None = None
    withdrawal_wallet_balance: float | None = None
    performance_bonus_balance: float | None = None
    referral_code: str | None = None

class PaymentUpdate(BaseModel):
    amount: float | None = None
    period: str | None = None
    status: str | None = None
    type: str | None = None
    payment_method: str | None = None
    network: str | None = None
    proof_url: str | None = None
    admin_notes: str | None = None
    payout_date: datetime | None = None

class ReferralCodeCreate(BaseModel):
    user_id: int
    code: str

class ReferralCodeUpdate(BaseModel):
    code: str | None = None
    signups_count: int | None = None
    trained_count: int | None = None
    earned_amount: float | None = None
    task_rebate_amount: float | None = None

class ReferralRelationshipCreate(BaseModel):
    user_id: int
    referrer_id: int
    referral_code_used: str | None = None

class ReferralRelationshipUpdate(BaseModel):
    referrer_id: int | None = None
    referral_code_used: str | None = None

# --- Router & Dependencies ---

router = APIRouter()

# --- Endpoints ---

@router.get("/admin/stats")
async def get_admin_stats(
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user),
):
    """Serve the high-frequency aggregate admin dashboard from a short TTL cache."""
    async def load_admin_stats() -> dict:
        users_count = await db.execute(select(func.count(User.id)))
        pending_payments = await db.execute(select(func.count(Payment.id)).filter(Payment.status == "pending"))
        total_payouts = await db.execute(
            select(func.sum(Payment.amount)).filter(Payment.status == "paid", Payment.type == "payout")
        )
        total_deposits = await db.execute(
            select(func.sum(Payment.amount)).filter(Payment.status == "paid", Payment.type == "deposit")
        )
        return {
            "total_users": users_count.scalar() or 0,
            "pending_payments": pending_payments.scalar() or 0,
            "total_payouts": total_payouts.scalar() or 0.0,
            "total_deposits": total_deposits.scalar() or 0.0,
        }

    return await cache.get_or_set(CacheKeys.admin_stats(), CacheTTL.ADMIN_STATS, load_admin_stats)

# Video Tasks
@router.post("/admin/video-tasks")
async def create_video_task(
    task_data: VideoTaskCreate,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    db_video_task = VideoTask(**task_data.dict())
    db.add(db_video_task)
    await db.commit()
    await db.refresh(db_video_task)
    return db_video_task

@router.get("/admin/video-tasks")
async def get_all_video_tasks(
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    result = await db.execute(select(VideoTask).options(selectinload(VideoTask.plan)))
    tasks = result.scalars().all()
    return [
        {
            "id": t.id,
            "plan_id": t.plan_id,
            "title": t.title,
            "description": t.description,
            "video_url": t.video_url,
            "reward_amount": t.reward_amount,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "plan": {
                "id": t.plan.id,
                "name": t.plan.name
            } if t.plan else None
        } for t in tasks
    ]

@router.get("/admin/video-tasks/{task_id}")
async def get_video_task_by_id(
    task_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    result = await db.execute(select(VideoTask).filter(VideoTask.id == task_id))
    video_task = result.scalar_one_or_none()
    if not video_task:
        raise HTTPException(status_code=404, detail="Video task not found")
    return video_task

@router.put("/admin/video-tasks/{task_id}")
async def update_video_task(
    task_id: int,
    task_data: VideoTaskUpdate,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    result = await db.execute(select(VideoTask).filter(VideoTask.id == task_id))
    video_task = result.scalar_one_or_none()
    if not video_task:
        raise HTTPException(status_code=404, detail="Video task not found")
    
    for field, value in task_data.dict(exclude_unset=True).items():
        setattr(video_task, field, value)
    
    await db.commit()
    await db.refresh(video_task)
    return video_task

@router.delete("/admin/video-tasks/{task_id}")
async def delete_video_task(
    task_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    from sqlalchemy.exc import IntegrityError
    result = await db.execute(select(VideoTask).filter(VideoTask.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Video task not found")
    
    try:
        # Clean up user progress for this task
        await db.execute(text("DELETE FROM user_video_tasks WHERE video_task_id = :tid"), {"tid": task_id})
        
        await db.delete(task)
        await db.commit()
        return {"message": "Video task deleted successfully"}
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=f"Cannot delete task: {str(e.orig)}")

# Certifications
@router.post("/admin/certifications")
async def create_certification(
    cert_data: CertificationCreate,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    db_cert = Certification(**cert_data.dict())
    db.add(db_cert)
    await db.commit()
    await db.refresh(db_cert)
    return db_cert

@router.get("/admin/certifications")
async def get_all_certifications(
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    result = await db.execute(select(Certification))
    certs = result.scalars().all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "description": c.description,
            "estimated_time": c.estimated_time,
            "video_url": c.video_url,
            "steps_count": c.steps_count,
            "is_active": c.is_active
        } for c in certs
    ]

@router.get("/admin/certifications/{cert_id}")
async def get_certification_by_id(
    cert_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    result = await db.execute(select(Certification).filter(Certification.id == cert_id))
    cert = result.scalar_one_or_none()
    if not cert:
        raise HTTPException(status_code=404, detail="Certification not found")
    return cert

@router.put("/admin/certifications/{cert_id}")
async def update_certification(
    cert_id: int,
    cert_data: CertificationUpdate,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    result = await db.execute(select(Certification).filter(Certification.id == cert_id))
    cert = result.scalar_one_or_none()
    if not cert:
        raise HTTPException(status_code=404, detail="Certification not found")
    
    for field, value in cert_data.dict(exclude_unset=True).items():
        setattr(cert, field, value)
    
    await db.commit()
    await db.refresh(cert)
    return cert

@router.delete("/admin/certifications/{cert_id}")
async def delete_certification(
    cert_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    result = await db.execute(select(Certification).filter(Certification.id == cert_id))
    cert = result.scalar_one_or_none()
    if not cert:
        raise HTTPException(status_code=404, detail="Certification not found")
    
    await db.delete(cert)
    await db.commit()
    return {"message": "Certification deleted"}

# Users
@router.get("/admin/users")
async def get_all_users(
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    users = result.scalars().all()
    return [
        {
            "id": u.id,
            "username": u.username,
            "first_name": u.first_name,
            "last_name": u.last_name,
            "email": u.email,
            "phone_number": u.phone_number,
            "role": u.role,
            "is_admin": u.is_admin,
            "is_suspended": u.is_suspended,
            "is_trained": u.is_trained,
            "deposit_wallet_balance": u.deposit_wallet_balance,
            "withdrawal_wallet_balance": u.withdrawal_wallet_balance,
            "performance_bonus_balance": u.performance_bonus_balance,
            "referral_code": u.referral_code,
            "created_at": u.created_at.isoformat() if u.created_at else None
        } for u in users
    ]

@router.get("/admin/users/{user_id}")
async def get_user_by_id(
    user_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    result = await db.execute(select(User).filter(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

@router.put("/admin/users/{user_id}")
async def update_user(
    user_id: int,
    user_data: UserUpdate,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    result = await db.execute(select(User).filter(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    for field, value in user_data.dict(exclude_unset=True).items():
        setattr(user, field, value)
    
    await db.commit()
    await db.refresh(user)
    return user

@router.delete("/admin/users/{user_id}")
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user),
):
    if current_user.id == user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Administrators cannot delete their own account from this endpoint")

    result = await db.execute(select(User).filter(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    try:
        # The schema’s foreign keys are responsible for deleting only the
        # target user's dependent records. Referral relationships are removed,
        # but referred users are never deleted as an unintended side effect.
        await db.delete(user)
        await db.commit()
        return {"message": "User deleted successfully"}
    except Exception:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User could not be deleted because of an active dependency")

# Payments
@router.get("/admin/payments")
async def get_all_payments(
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    result = await db.execute(
        select(Payment).options(selectinload(Payment.user)).order_by(Payment.created_at.desc())
    )
    payments = result.scalars().all()
    return [
        {
            "id": p.id,
            "user_id": p.user_id,
            "amount": p.amount,
            "period": p.period,
            "status": p.status,
            "type": p.type,
            "payment_method": p.payment_method,
            "network": p.network,
            "proof_url": p.proof_url,
            "admin_notes": p.admin_notes,
            "destination_number": p.destination_number,
            "payout_date": p.payout_date.isoformat() if p.payout_date else None,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "user": {
                "id": p.user.id,
                "email": p.user.email,
                "first_name": p.user.first_name,
                "last_name": p.user.last_name
            } if p.user else None
        } for p in payments
    ]

@router.post("/admin/payments/{payment_id}/approve")
async def approve_payment(
    payment_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    result = await db.execute(select(Payment).filter(Payment.id == payment_id))
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    
    if payment.status != "pending":
        raise HTTPException(status_code=400, detail="Payment is not pending")
    
    payment.status = "paid"
    
    result = await db.execute(select(User).filter(User.id == payment.user_id))
    user = result.scalar_one_or_none()
    
    if payment.type == "deposit":
        if user:
            user.deposit_wallet_balance += payment.amount
    elif payment.type == "payout" or payment.type == "withdrawal":
        # For withdrawals, the balance is usually deducted at the time of request.
        # Here we just mark it as paid.
        payment.payout_date = datetime.now()
    
    await db.commit()
    return {"message": "Payment approved"}

class RejectRequest(BaseModel):
    admin_notes: str

@router.post("/admin/payments/{payment_id}/reject")
async def reject_payment(
    payment_id: int,
    reject_data: RejectRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    result = await db.execute(select(Payment).filter(Payment.id == payment_id))
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    
    if payment.status != "pending":
        raise HTTPException(status_code=400, detail="Payment is not pending")
    
    payment.status = "rejected"
    payment.admin_notes = reject_data.admin_notes
    
    # If rejecting a withdrawal, refund the user
    if payment.type == "payout" or payment.type == "withdrawal":
        result = await db.execute(select(User).filter(User.id == payment.user_id))
        user = result.scalar_one_or_none()
        if user:
            user.withdrawal_wallet_balance += payment.amount
            
    await db.commit()
    return {"message": "Payment rejected and balance refunded if applicable"}

@router.get("/admin/payments/{payment_id}")
async def get_payment_by_id(
    payment_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    result = await db.execute(select(Payment).filter(Payment.id == payment_id))
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    return payment

@router.put("/admin/payments/{payment_id}")
async def update_payment(
    payment_id: int,
    payment_data: PaymentUpdate,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    result = await db.execute(select(Payment).filter(Payment.id == payment_id))
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    
    for field, value in payment_data.dict(exclude_unset=True).items():
        setattr(payment, field, value)
    
    await db.commit()
    await db.refresh(payment)
    return payment

@router.delete("/admin/payments/{payment_id}")
async def delete_payment(
    payment_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    result = await db.execute(select(Payment).filter(Payment.id == payment_id))
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    
    await db.delete(payment)
    await db.commit()
    return {"message": "Payment deleted successfully"}

# Plans
@router.post("/admin/plans")
async def create_plan(
    plan_data: PlanCreate,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    db_plan = Plan(**plan_data.dict())
    db.add(db_plan)
    await db.commit()
    await db.refresh(db_plan)
    return db_plan

@router.get("/admin/plans")
async def get_all_plans(
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    result = await db.execute(select(Plan))
    return result.scalars().all()

@router.get("/admin/plans/{plan_id}")
async def get_plan_by_id(
    plan_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    result = await db.execute(select(Plan).filter(Plan.id == plan_id))
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan

@router.put("/admin/plans/{plan_id}")
async def update_plan(
    plan_id: int,
    plan_data: PlanUpdate,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    result = await db.execute(select(Plan).filter(Plan.id == plan_id))
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    
    for field, value in plan_data.dict(exclude_unset=True).items():
        setattr(plan, field, value)
    
    await db.commit()
    await db.refresh(plan)
    return plan

@router.delete("/admin/plans/{plan_id}")
async def delete_plan(
    plan_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    from sqlalchemy.exc import IntegrityError
    
    result = await db.execute(select(Plan).filter(Plan.id == plan_id))
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    
    try:
        # Check if any users are still using this plan
        user_check = await db.execute(select(User).filter(User.current_plan_id == plan_id).limit(1))
        if user_check.scalar_one_or_none():
            raise HTTPException(
                status_code=400, 
                detail="Cannot delete plan: There are users currently subscribed to it. Change their plans first."
            )
            
        await db.delete(plan)
        await db.commit()
        return {"message": "Plan deleted successfully"}
    except HTTPException:
        raise
    except IntegrityError as e:
        await db.rollback()
        raise HTTPException(
            status_code=400, 
            detail=f"Cannot delete plan due to database constraints. Error: {str(e.orig)}"
        )
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"An error occurred: {str(e)}")

# Referral Codes
@router.post("/admin/referral-codes")
async def create_referral_code(
    referral_code_data: ReferralCodeCreate,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    db_referral_code = ReferralCode(**referral_code_data.dict())
    db.add(db_referral_code)
    await db.commit()
    await db.refresh(db_referral_code)
    return db_referral_code

@router.get("/admin/referral-codes")
async def get_all_referral_codes(
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    result = await db.execute(select(ReferralCode))
    return result.scalars().all()

@router.get("/admin/referral-codes/{referral_code_id}")
async def get_referral_code_by_id(
    referral_code_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    result = await db.execute(select(ReferralCode).filter(ReferralCode.id == referral_code_id))
    referral_code = result.scalar_one_or_none()
    if not referral_code:
        raise HTTPException(status_code=404, detail="Referral code not found")
    return referral_code

@router.put("/admin/referral-codes/{referral_code_id}")
async def update_referral_code(
    referral_code_id: int,
    referral_code_data: ReferralCodeUpdate,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    result = await db.execute(select(ReferralCode).filter(ReferralCode.id == referral_code_id))
    referral_code = result.scalar_one_or_none()
    if not referral_code:
        raise HTTPException(status_code=404, detail="Referral code not found")
    
    for field, value in referral_code_data.dict(exclude_unset=True).items():
        setattr(referral_code, field, value)
    
    await db.commit()
    await db.refresh(referral_code)
    return referral_code

@router.delete("/admin/referral-codes/{referral_code_id}")
async def delete_referral_code(
    referral_code_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    result = await db.execute(select(ReferralCode).filter(ReferralCode.id == referral_code_id))
    referral_code = result.scalar_one_or_none()
    if not referral_code:
        raise HTTPException(status_code=404, detail="Referral code not found")
    
    await db.delete(referral_code)
    await db.commit()
    return {"message": "Referral code deleted successfully"}

# Referral Relationships
@router.post("/admin/referral-relationships")
async def create_referral_relationship(
    referral_relationship_data: ReferralRelationshipCreate,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    db_referral_relationship = ReferralRelationship(**referral_relationship_data.dict())
    db.add(db_referral_relationship)
    await db.commit()
    await db.refresh(db_referral_relationship)
    return db_referral_relationship

@router.get("/admin/referral-relationships")
async def get_all_referral_relationships(
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    result = await db.execute(select(ReferralRelationship))
    return result.scalars().all()

@router.get("/admin/referral-relationships/{referral_relationship_id}")
async def get_referral_relationship_by_id(
    referral_relationship_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    result = await db.execute(select(ReferralRelationship).filter(ReferralRelationship.id == referral_relationship_id))
    referral_relationship = result.scalar_one_or_none()
    if not referral_relationship:
        raise HTTPException(status_code=404, detail="Referral relationship not found")
    return referral_relationship

@router.put("/admin/referral-relationships/{referral_relationship_id}")
async def update_referral_relationship(
    referral_relationship_id: int,
    referral_relationship_data: ReferralRelationshipUpdate,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    result = await db.execute(select(ReferralRelationship).filter(ReferralRelationship.id == referral_relationship_id))
    referral_relationship = result.scalar_one_or_none()
    if not referral_relationship:
        raise HTTPException(status_code=404, detail="Referral relationship not found")
    
    for field, value in referral_relationship_data.dict(exclude_unset=True).items():
        setattr(referral_relationship, field, value)
    
    await db.commit()
    await db.refresh(referral_relationship)
    return referral_relationship

@router.delete("/admin/referral-relationships/{referral_relationship_id}")
async def delete_referral_relationship(
    referral_relationship_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    result = await db.execute(select(ReferralRelationship).filter(ReferralRelationship.id == referral_relationship_id))
    referral_relationship = result.scalar_one_or_none()
    if not referral_relationship:
        raise HTTPException(status_code=404, detail="Referral relationship not found")
    
    await db.delete(referral_relationship)
    await db.commit()
    return {"message": "Referral relationship deleted successfully"}
