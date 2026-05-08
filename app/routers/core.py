from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database.database import get_async_db
from app.routers.auth import get_current_user
from app.models import models

router = APIRouter()

class DashboardSummary(BaseModel):
    footage_labeled_min: int
    approved_roles: str
    certifications_earned: int

class CertificationSchema(BaseModel):
    id: int
    name: str
    status: str

    class Config:
        orm_mode = True

class LearningHubContent(BaseModel):
    guidelines: str
    references: str
    training_videos: str

@router.get("/dashboard/summary", response_model=DashboardSummary)
async def get_dashboard_summary(
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    # Calculate stats from DB
    result = await db.execute(
        select(models.UserCertification).filter(
            models.UserCertification.user_id == current_user.id,
            models.UserCertification.status == "completed"
        )
    )
    completed_certs = len(result.scalars().all())
    
    return {
        "footage_labeled_min": 0, # Placeholder for actual task stats
        "approved_roles": "None yet",
        "certifications_earned": completed_certs
    }

@router.get("/training/certifications", response_model=List[CertificationSchema])
async def get_certifications(
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    # Get all certifications and user's status
    result = await db.execute(select(models.Certification))
    all_certs = result.scalars().all()
    
    user_certs_result = await db.execute(
        select(models.UserCertification).filter(models.UserCertification.user_id == current_user.id)
    )
    user_certs = {uc.certification_id: uc.status for uc in user_certs_result.scalars().all()}
    
    response = []
    for cert in all_certs:
        status = user_certs.get(cert.id, "available")
        response.append({"id": cert.id, "name": cert.name, "status": status})
    
    return response

@router.get("/training/learning-hub", response_model=LearningHubContent)
async def get_learning_hub(current_user: models.User = Depends(get_current_user)):
    return {
        "guidelines": "Each video is divided into multiple events (segments)...",
        "references": "Reference materials for labeling...",
        "training_videos": "https://example.com/training-video.mp4"
    }
