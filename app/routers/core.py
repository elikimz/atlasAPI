from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.models import models
from app.auth.auth import get_current_user
from app.schemas import AvailableTask, UserTaskCompletion, Certification as CertificationSchema # Import Certification as CertificationSchema to avoid name collision

router = APIRouter()

# --- Task Endpoints ---

@router.get("/tasks/available", response_model=List[AvailableTask])
def get_available_tasks(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    # Fetch all video tasks
    all_video_tasks = db.query(models.VideoTask).all()

    # Fetch tasks already completed by the current user
    completed_task_ids = [uvt.video_task_id for uvt in current_user.video_tasks if uvt.status == "completed"]

    # Filter out tasks already completed by the user
    available_tasks = [
        task for task in all_video_tasks if task.id not in completed_task_ids
    ]

    return available_tasks

@router.post("/tasks/complete", status_code=status.HTTP_200_OK)
def complete_task(task_completion: UserTaskCompletion, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    video_task = db.query(models.VideoTask).filter(models.VideoTask.id == task_completion.video_task_id).first()
    if not video_task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video task not found")

    # Check if user has already completed this task
    user_video_task = db.query(models.UserVideoTask).filter(
        models.UserVideoTask.user_id == current_user.id,
        models.UserVideoTask.video_task_id == task_completion.video_task_id
    ).first()

    if user_video_task and user_video_task.status == "completed":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Task already completed by user")

    if not user_video_task:
        # Create a new entry for completed task
        user_video_task = models.UserVideoTask(
            user_id=current_user.id,
            video_task_id=video_task.id,
            status="completed",
            completed_at=datetime.utcnow()
        )
        db.add(user_video_task)
    else:
        # Update existing entry if it was pending/rejected
        user_video_task.status = "completed"
        user_video_task.completed_at = datetime.utcnow()

    current_user.withdrawal_wallet_balance += video_task.reward_amount

    db.commit()
    db.refresh(current_user)
    db.refresh(user_video_task)

    return {"message": "Task completed successfully and withdrawal wallet updated"}

# --- Existing Endpoints (adapted to use Session and get_db) ---

class DashboardSummary(BaseModel):
    footage_labeled_min: int
    approved_roles: str
    certifications_earned: int

class LearningHubContent(BaseModel):
    guidelines: str
    references: str
    training_videos: str

@router.get("/dashboard/summary", response_model=DashboardSummary)
def get_dashboard_summary(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    completed_certs = db.query(models.UserCertification).filter(
        models.UserCertification.user_id == current_user.id,
        models.UserCertification.status == "completed"
    ).count()
    
    return {
        "footage_labeled_min": 0,
        "approved_roles": "None yet",
        "certifications_earned": completed_certs
    }

@router.get("/training/certifications", response_model=List[CertificationSchema])
def get_certifications(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    all_certs = db.query(models.Certification).all()
    
    user_certs = {uc.certification_id: uc.status for uc in db.query(models.UserCertification).filter(models.UserCertification.user_id == current_user.id).all()}
    
    response = []
    for cert in all_certs:
        status = user_certs.get(cert.id, "available")
        response.append({"id": cert.id, "name": cert.name, "status": status})
    
    return response

@router.post("/training/certifications/{id}/start", response_model=dict)
def start_certification(
    id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    cert = db.query(models.Certification).filter(models.Certification.id == id).first()
    if not cert:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Certification not found")
    
    user_cert = db.query(models.UserCertification).filter(
        models.UserCertification.user_id == current_user.id,
        models.UserCertification.certification_id == id
    ).first()
    
    if user_cert:
        return {"message": f"Certification already {user_cert.status}"}
    
    new_user_cert = models.UserCertification(
        user_id=current_user.id,
        certification_id=id,
        status="in_progress",
        started_at=datetime.utcnow()
    )
    db.add(new_user_cert)
    db.commit()
    
    return {"message": "Certification started"}

@router.get("/training/learning-hub", response_model=LearningHubContent)
def get_learning_hub(current_user: models.User = Depends(get_current_user)):
    return {
        "guidelines": "Each video is divided into multiple events (segments)...",
        "references": "Reference materials for labeling...",
        "training_videos": "https://example.com/training-video.mp4"
    }
