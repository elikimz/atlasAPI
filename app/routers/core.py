from typing import List, Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.routers.auth import get_current_user
from app.models import models

router = APIRouter()

class DashboardSummary(BaseModel):
    footage_labeled_min: int
    approved_roles: str
    certifications_earned: int

class Certification(BaseModel):
    id: int
    name: str
    status: str

class LearningHubContent(BaseModel):
    guidelines: str
    references: str
    training_videos: str

@router.get("/dashboard/summary", response_model=DashboardSummary)
async def get_dashboard_summary(current_user: models.User = Depends(get_current_user)):
    # Mock data based on Atlas Capture UI
    return {
        "footage_labeled_min": 0,
        "approved_roles": "None yet",
        "certifications_earned": 0
    }

@router.get("/training/certifications", response_model=List[Certification])
async def get_certifications(current_user: models.User = Depends(get_current_user)):
    # Mock data based on Atlas Capture UI
    return [
        {"id": 1, "name": "Standard Label Training", "status": "available"},
        {"id": 2, "name": "Easy Mode Training", "status": "coming soon"},
        {"id": 3, "name": "Auditor Certification", "status": "coming soon"}
    ]

@router.get("/training/learning-hub", response_model=LearningHubContent)
async def get_learning_hub(current_user: models.User = Depends(get_current_user)):
    # Mock data based on Atlas Capture UI
    return {
        "guidelines": "Each video is divided into multiple events (segments)...",
        "references": "Reference materials for labeling...",
        "training_videos": "https://example.com/training-video.mp4"
    }
