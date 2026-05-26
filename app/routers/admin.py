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

class VideoTaskCreate(BaseModel):
    title: str
    description: str
    reward_amount: float
    video_url: str

router = APIRouter()

# Configure Cloudinary
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
)

async def get_current_admin_user(current_user: User = Depends(get_current_user)):
    if not current_user.is_admin:
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
        upload_result = cloudinary.uploader.upload(file.file, resource_type="video")
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
        upload_result = cloudinary.uploader.upload(file.file, resource_type="video")
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
