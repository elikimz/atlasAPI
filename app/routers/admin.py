from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from sqlalchemy.orm import Session
from app.database.database import get_db
from app.models.models import User, VideoTask
from app.schemas.schemas import VideoTaskCreate
from app.auth.auth import get_current_user
import cloudinary
import cloudinary.uploader
import os

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

@router.post("/admin/upload-video", response_model=VideoTaskCreate)
async def upload_video(
    title: str,
    description: str,
    reward_amount: float,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    try:
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
        db.commit()
        db.refresh(db_video_task)
        return db_video_task
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

@router.get("/admin/video-tasks")
async def get_all_video_tasks(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    video_tasks = db.query(VideoTask).all()
    return video_tasks
