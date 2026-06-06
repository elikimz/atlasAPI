from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database.database import get_async_db
from app.models.models import User, VideoTask
from app.routers.auth import get_current_user
from pydantic import BaseModel
import cloudinary
import cloudinary.uploader
import os
from datetime import datetime
from app.config import settings

# Configure Cloudinary
cloudinary.config(
    cloud_name=settings.CLOUDINARY_CLOUD_NAME,
    api_key=settings.CLOUDINARY_API_KEY,
    api_secret=settings.CLOUDINARY_API_SECRET,
)

class VideoTaskCreate(BaseModel):
    title: str
    description: str
    reward_amount: float
    video_url: str

class PlanCreate(BaseModel):
    name: str
    price: float
    daily_tasks_limit: int
    validity_days: int
    description: str
    is_active: bool = True
    is_upgrade_only: bool = False

@router.post("/admin/plans", response_model=PlanCreate)
async def create_plan(
    plan_data: PlanCreate,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    from app.models.models import Plan
    db_plan = Plan(**plan_data.dict())
    db.add(db_plan)
    await db.commit()
    await db.refresh(db_plan)
    return db_plan

class VideoTaskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    reward_amount: float | None = None
    video_url: str | None = None

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
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    role: str | None = None
    is_admin: bool | None = None
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

class PlanUpdate(BaseModel):
    name: str | None = None
    price: float | None = None
    daily_tasks_limit: int | None = None
    validity_days: int | None = None
    description: str | None = None
    is_active: bool | None = None
    is_upgrade_only: bool | None = None

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

router = APIRouter()

async def get_current_admin_user(current_user: User = Depends(get_current_user)):
    if current_user.role != "admin" and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions")
    return current_user

@router.post("/admin/upload-video")
async def upload_video(
    title: str,
    description: str,
    reward_amount: float,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    try:
        # Note: cloudinary upload is synchronous, but in a real async environment 
        # you might want to run this in a threadpool
        upload_result = cloudinary.uploader.upload(
            file.file, 
            resource_type="video",
            cloud_name=settings.CLOUDINARY_CLOUD_NAME,
            api_key=settings.CLOUDINARY_API_KEY,
            api_secret=settings.CLOUDINARY_API_SECRET
        )
        video_url = upload_result.get("secure_url")

        if not video_url:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to upload video to Cloudinary")

        db_video_task = VideoTask(
            title=title,
            description=description,
            video_url=video_url,
            reward_amount=reward_amount
        )
        db.add(db_video_task)
        await db.commit()
        await db.refresh(db_video_task)
        return db_video_task
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.post("/admin/create-video-task")
async def create_video_task(
    task_data: VideoTaskCreate,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    try:
        db_video_task = VideoTask(
            title=task_data.title,
            description=task_data.description,
            video_url=task_data.video_url,
            reward_amount=task_data.reward_amount
        )
        db.add(db_video_task)
        await db.commit()
        await db.refresh(db_video_task)
        return db_video_task
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.get("/admin/video-tasks", response_model=list[VideoTaskCreate])
async def get_all_video_tasks(
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    result = await db.execute(select(VideoTask))
    video_tasks = result.scalars().all()
    return video_tasks

@router.get("/admin/video-tasks/{task_id}", response_model=VideoTaskCreate)
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

@router.put("/admin/video-tasks/{task_id}", response_model=VideoTaskCreate)
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
    result = await db.execute(select(VideoTask).filter(VideoTask.id == task_id))
    video_task = result.scalar_one_or_none()
    if not video_task:
        raise HTTPException(status_code=404, detail="Video task not found")
    
    await db.delete(video_task)
    await db.commit()
    return {"message": "Video task deleted successfully"}

@router.post("/admin/certifications", response_model=CertificationCreate)
async def create_certification(
    cert_data: CertificationCreate,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    from app.models.models import Certification
    db_cert = Certification(**cert_data.dict())
    db.add(db_cert)
    await db.commit()
    await db.refresh(db_cert)
    return db_cert

@router.get("/admin/certifications", response_model=list[CertificationCreate])
async def get_all_certifications(
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    from app.models.models import Certification
    result = await db.execute(select(Certification))
    return result.scalars().all()

@router.get("/admin/certifications/{cert_id}", response_model=CertificationCreate)
async def get_certification_by_id(
    cert_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    from app.models.models import Certification
    result = await db.execute(select(Certification).filter(Certification.id == cert_id))
    cert = result.scalar_one_or_none()
    if not cert:
        raise HTTPException(status_code=404, detail="Certification not found")
    return cert

@router.put("/admin/certifications/{cert_id}", response_model=CertificationCreate)
async def update_certification(
    cert_id: int,
    cert_data: CertificationUpdate,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    from app.models.models import Certification
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
    from app.models.models import Certification
    result = await db.execute(select(Certification).filter(Certification.id == cert_id))
    cert = result.scalar_one_or_none()
    if not cert:
        raise HTTPException(status_code=404, detail="Certification not found")
    
    await db.delete(cert)
    await db.commit()
    return {"message": "Certification deleted"}

@router.get("/admin/payments")
async def get_all_payments(
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    from app.models.models import Payment, User
    from sqlalchemy.orm import selectinload
    result = await db.execute(
        select(Payment).options(selectinload(Payment.user)).order_by(Payment.created_at.desc())
    )
    payments = result.scalars().all()
    return payments

@router.post("/admin/payments/{payment_id}/approve")
async def approve_payment(
    payment_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    from app.models.models import Payment, User
    result = await db.execute(select(Payment).filter(Payment.id == payment_id))
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    
    if payment.status != "pending":
        raise HTTPException(status_code=400, detail="Payment is not pending")
    
    payment.status = "paid"
    
    # If it's a deposit, update user balance
    if payment.type == "deposit":
        result = await db.execute(select(User).filter(User.id == payment.user_id))
        user = result.scalar_one_or_none()
        if user:
            user.deposit_wallet_balance += payment.amount
    
    await db.commit()
    return {"message": "Payment approved"}

@router.post("/admin/payments/{payment_id}/reject")
async def reject_payment(
    payment_id: int,
    admin_notes: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    from app.models.models import Payment
    result = await db.execute(select(Payment).filter(Payment.id == payment_id))
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    
    payment.status = "rejected"
    payment.admin_notes = admin_notes
    await db.commit()
    return {"message": "Payment rejected"}

@router.get("/admin/payments/{payment_id}", response_model=PaymentUpdate)
async def get_payment_by_id(
    payment_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    from app.models.models import Payment
    result = await db.execute(select(Payment).filter(Payment.id == payment_id))
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    return payment

@router.put("/admin/payments/{payment_id}", response_model=PaymentUpdate)
async def update_payment(
    payment_id: int,
    payment_data: PaymentUpdate,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    from app.models.models import Payment
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
    from app.models.models import Payment
    result = await db.execute(select(Payment).filter(Payment.id == payment_id))
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    
    await db.delete(payment)
    await db.commit()
    return {"message": "Payment deleted successfully"}

# --- User Management ---
@router.get("/admin/users")
async def get_all_users(
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    return result.scalars().all()

@router.get("/admin/users/{user_id}", response_model=UserUpdate)
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

@router.put("/admin/users/{user_id}", response_model=UserUpdate)
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
        if field == "role":
            if value not in ["user", "admin"]:
                raise HTTPException(status_code=400, detail="Invalid role")
            user.role = value
            user.is_admin = (value == "admin")
        else:
            setattr(user, field, value)
    
    await db.commit()
    await db.refresh(user)
    return user

@router.delete("/admin/users/{user_id}")
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    result = await db.execute(select(User).filter(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    await db.delete(user)
    await db.commit()
    return {"message": "User deleted successfully"}

@router.put("/admin/users/{user_id}/role")
async def update_user_role(
    user_id: int,
    role: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    if role not in ["user", "admin"]:
        raise HTTPException(status_code=400, detail="Invalid role")
    
    result = await db.execute(select(User).filter(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    user.role = role
    user.is_admin = (role == "admin")
    await db.commit()
    return {"message": f"User role updated to {role}"}

# --- Plan Management ---
@router.get("/admin/plans", response_model=list[PlanCreate])
async def get_all_plans(
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    from app.models.models import Plan
    result = await db.execute(select(Plan))
    return result.scalars().all()

@router.get("/admin/plans/{plan_id}", response_model=PlanCreate)
async def get_plan_by_id(
    plan_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    from app.models.models import Plan
    result = await db.execute(select(Plan).filter(Plan.id == plan_id))
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan

@router.put("/admin/plans/{plan_id}", response_model=PlanCreate)
async def update_plan(
    plan_id: int,
    plan_data: PlanUpdate,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    from app.models.models import Plan
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
    from app.models.models import Plan
    result = await db.execute(select(Plan).filter(Plan.id == plan_id))
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    
    await db.delete(plan)
    await db.commit()
    return {"message": "Plan deleted successfully"}



# --- Dashboard Stats ---
@router.get("/admin/stats")
async def get_admin_stats(
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    from app.models.models import User, Payment, VideoTask, UserVideoTask
    from sqlalchemy import func
    
    users_count = await db.execute(select(func.count(User.id)))
    pending_payments = await db.execute(select(func.count(Payment.id)).filter(Payment.status == "pending"))
    total_payouts = await db.execute(select(func.sum(Payment.amount)).filter(Payment.status == "paid", Payment.type == "payout"))
    total_deposits = await db.execute(select(func.sum(Payment.amount)).filter(Payment.status == "paid", Payment.type == "deposit"))
    
    return {
        "total_users": users_count.scalar(),
        "pending_payments": pending_payments.scalar(),
        "total_payouts": total_payouts.scalar() or 0.0,
        "total_deposits": total_deposits.scalar() or 0.0
    }

# --- Referral Code Management ---
@router.post("/admin/referral-codes", response_model=ReferralCodeCreate)
async def create_referral_code(
    referral_code_data: ReferralCodeCreate,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    from app.models.models import ReferralCode
    db_referral_code = ReferralCode(**referral_code_data.dict())
    db.add(db_referral_code)
    await db.commit()
    await db.refresh(db_referral_code)
    return db_referral_code

@router.get("/admin/referral-codes", response_model=list[ReferralCodeCreate])
async def get_all_referral_codes(
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    from app.models.models import ReferralCode
    result = await db.execute(select(ReferralCode))
    return result.scalars().all()

@router.get("/admin/referral-codes/{referral_code_id}", response_model=ReferralCodeCreate)
async def get_referral_code_by_id(
    referral_code_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    from app.models.models import ReferralCode
    result = await db.execute(select(ReferralCode).filter(ReferralCode.id == referral_code_id))
    referral_code = result.scalar_one_or_none()
    if not referral_code:
        raise HTTPException(status_code=404, detail="Referral code not found")
    return referral_code

@router.put("/admin/referral-codes/{referral_code_id}", response_model=ReferralCodeCreate)
async def update_referral_code(
    referral_code_id: int,
    referral_code_data: ReferralCodeUpdate,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    from app.models.models import ReferralCode
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
    from app.models.models import ReferralCode
    result = await db.execute(select(ReferralCode).filter(ReferralCode.id == referral_code_id))
    referral_code = result.scalar_one_or_none()
    if not referral_code:
        raise HTTPException(status_code=404, detail="Referral code not found")
    
    await db.delete(referral_code)
    await db.commit()
    return {"message": "Referral code deleted successfully"}

# --- Referral Relationship Management ---
@router.post("/admin/referral-relationships", response_model=ReferralRelationshipCreate)
async def create_referral_relationship(
    referral_relationship_data: ReferralRelationshipCreate,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    from app.models.models import ReferralRelationship
    db_referral_relationship = ReferralRelationship(**referral_relationship_data.dict())
    db.add(db_referral_relationship)
    await db.commit()
    await db.refresh(db_referral_relationship)
    return db_referral_relationship

@router.get("/admin/referral-relationships", response_model=list[ReferralRelationshipCreate])
async def get_all_referral_relationships(
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    from app.models.models import ReferralRelationship
    result = await db.execute(select(ReferralRelationship))
    return result.scalars().all()

@router.get("/admin/referral-relationships/{referral_relationship_id}", response_model=ReferralRelationshipCreate)
async def get_referral_relationship_by_id(
    referral_relationship_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    from app.models.models import ReferralRelationship
    result = await db.execute(select(ReferralRelationship).filter(ReferralRelationship.id == referral_relationship_id))
    referral_relationship = result.scalar_one_or_none()
    if not referral_relationship:
        raise HTTPException(status_code=404, detail="Referral relationship not found")
    return referral_relationship

@router.put("/admin/referral-relationships/{referral_relationship_id}", response_model=ReferralRelationshipCreate)
async def update_referral_relationship(
    referral_relationship_id: int,
    referral_relationship_data: ReferralRelationshipUpdate,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    from app.models.models import ReferralRelationship
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
    from app.models.models import ReferralRelationship
    result = await db.execute(select(ReferralRelationship).filter(ReferralRelationship.id == referral_relationship_id))
    referral_relationship = result.scalar_one_or_none()
    if not referral_relationship:
        raise HTTPException(status_code=404, detail="Referral relationship not found")
    
    await db.delete(referral_relationship)
    await db.commit()
    return {"message": "Referral relationship deleted successfully"}
