from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import get_async_db
from app.models import models
from app.routers.auth import get_current_user
from app.schemas import AvailableTask, UserTaskCompletion, Certification as CertificationSchema

router = APIRouter()

# --- Task Endpoints ---

@router.get("/tasks/available")
async def get_available_tasks(db: AsyncSession = Depends(get_async_db), current_user: models.User = Depends(get_current_user)):
    # Fetch all video tasks
    result = await db.execute(select(models.VideoTask))
    all_video_tasks = result.scalars().all()

    # Fetch tasks status for the current user
    uvt_result = await db.execute(
        select(models.UserVideoTask).filter(
            models.UserVideoTask.user_id == current_user.id
        )
    )
    user_tasks = {uvt.video_task_id: uvt.status for uvt in uvt_result.scalars().all()}

    # Return all tasks with their status
    response = []
    for task in all_video_tasks:
        status = user_tasks.get(task.id, "available")
        response.append({
            "id": task.id,
            "title": task.title,
            "description": task.description,
            "video_url": task.video_url,
            "reward_amount": task.reward_amount,
            "status": status
        })

    return response

@router.get("/tasks/all", response_model=List[AvailableTask])
async def get_all_tasks(db: AsyncSession = Depends(get_async_db), current_user: models.User = Depends(get_current_user)):
    """Return all video tasks regardless of completion status (used for task detail view)."""
    result = await db.execute(select(models.VideoTask))
    all_video_tasks = result.scalars().all()
    return all_video_tasks


@router.post("/tasks/complete", status_code=status.HTTP_200_OK)
async def complete_task(task_completion: UserTaskCompletion, db: AsyncSession = Depends(get_async_db), current_user: models.User = Depends(get_current_user)):
    vt_result = await db.execute(select(models.VideoTask).filter(models.VideoTask.id == task_completion.video_task_id))
    video_task = vt_result.scalar_one_or_none()
    
    if not video_task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video task not found")

    # Check if user has already completed this task
    uvt_result = await db.execute(
        select(models.UserVideoTask).filter(
            models.UserVideoTask.user_id == current_user.id,
            models.UserVideoTask.video_task_id == task_completion.video_task_id
        )
    )
    user_video_task = uvt_result.scalar_one_or_none()

    if user_video_task and user_video_task.status == "completed":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Task already completed by user")

    if not user_video_task:
        # Create a new entry for completed task
        user_video_task = models.UserVideoTask(
            user_id=current_user.id,
            video_task_id=video_task.id,
            status="completed",
            completed_at=datetime.now(timezone.utc)
        )
        db.add(user_video_task)
    else:
        # Update existing entry if it was pending/rejected
        user_video_task.status = "completed"
        user_video_task.completed_at = datetime.now(timezone.utc)

    # Update balance safely (only if column exists in DB)
    if hasattr(current_user, "withdrawal_wallet_balance"):
        current_user.withdrawal_wallet_balance = (current_user.withdrawal_wallet_balance or 0.0) + video_task.reward_amount
    
    await db.commit()
    await db.refresh(current_user)
    await db.refresh(user_video_task)

    return {"message": "Task completed successfully and withdrawal wallet updated"}

# --- Existing Endpoints ---

class DashboardSummary(BaseModel):
    footage_labeled_min: int
    approved_roles: str
    certifications_earned: int

class LearningHubContent(BaseModel):
    guidelines: str
    references: str
    training_videos: str

@router.get("/dashboard/summary", response_model=DashboardSummary)
async def get_dashboard_summary(
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    result = await db.execute(
        select(func.count(models.UserCertification.id)).filter(
            models.UserCertification.user_id == current_user.id,
            models.UserCertification.status == "completed"
        )
    )
    completed_certs = result.scalar()
    
    return {
        "footage_labeled_min": 0,
        "approved_roles": "None yet",
        "certifications_earned": completed_certs or 0
    }

@router.get("/training/certifications", response_model=List[CertificationSchema])
async def get_certifications(
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    result = await db.execute(select(models.Certification))
    all_certs = result.scalars().all()
    
    uc_result = await db.execute(
        select(models.UserCertification).filter(models.UserCertification.user_id == current_user.id)
    )
    user_certs = {uc.certification_id: uc.status for uc in uc_result.scalars().all()}
    
    response = []
    for cert in all_certs:
        status = user_certs.get(cert.id, "available")
        response.append({"id": cert.id, "name": cert.name, "status": status})
    
    return response

@router.post("/training/certifications/{id}/start", response_model=dict)
async def start_certification(
    id: int,
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    cert_result = await db.execute(select(models.Certification).filter(models.Certification.id == id))
    cert = cert_result.scalar_one_or_none()
    
    if not cert:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Certification not found")
    
    uc_result = await db.execute(
        select(models.UserCertification).filter(
            models.UserCertification.user_id == current_user.id,
            models.UserCertification.certification_id == id
        )
    )
    user_cert = uc_result.scalar_one_or_none()
    
    if user_cert:
        return {"message": f"Certification already {user_cert.status}"}
    
    new_user_cert = models.UserCertification(
        user_id=current_user.id,
        certification_id=id,
        status="in_progress",
        started_at=datetime.now(timezone.utc)
    )
    db.add(new_user_cert)
    await db.commit()
    
    return {"message": "Certification started"}

@router.get("/training/learning-hub", response_model=LearningHubContent)
async def get_learning_hub(current_user: models.User = Depends(get_current_user)):
    return {
        "guidelines": "Each video is divided into multiple events (segments)...",
        "references": "Reference materials for labeling...",
        "training_videos": "https://example.com/training-video.mp4"
    }
