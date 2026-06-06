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

@router.get("/admin/video-tasks")
async def get_all_video_tasks(
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    result = await db.execute(select(VideoTask))
    video_tasks = result.scalars().all()
    return video_tasks

@router.post("/admin/upload-training-video")
async def upload_training_video(
    name: str,
    description: str,
    estimated_time: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    try:
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

        from app.models.models import Certification
        db_cert = Certification(
            name=name,
            description=description,
            estimated_time=estimated_time,
            video_url=video_url,
            steps_count=1
        )
        db.add(db_cert)
        await db.commit()
        await db.refresh(db_cert)
        return db_cert
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.get("/admin/certifications")
async def get_admin_certifications(
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    from app.models.models import Certification
    result = await db.execute(select(Certification))
    return result.scalars().all()

@router.delete("/admin/certifications/{id}")
async def delete_certification(
    id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    from app.models.models import Certification
    result = await db.execute(select(Certification).filter(Certification.id == id))
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

# --- User Management ---
@router.get("/admin/users")
async def get_all_users(
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    return result.scalars().all()

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
@router.get("/admin/plans")
async def get_admin_plans(
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_admin_user)
):
    from app.models.models import Plan
    result = await db.execute(select(Plan))
    return result.scalars().all()

class PlanCreate(BaseModel):
    name: str
    price: float
    daily_tasks_limit: int
    validity_days: int
    description: str
    is_active: bool = True
    is_upgrade_only: bool = False

@router.post("/admin/plans")
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
