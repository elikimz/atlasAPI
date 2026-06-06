from datetime import datetime, timezone, timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from fpdf import FPDF

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
    # 1. Check Plan Validity
    if not current_user.current_plan_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No active plan found. Please purchase a plan to start earning.")
    
    if current_user.plan_expiry_date and current_user.plan_expiry_date < datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Your plan has expired. Please upgrade to a higher tier to continue earning.")

    # 2. Check Daily Task Limit
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    daily_count_result = await db.execute(
        select(func.count(models.UserVideoTask.id))
        .filter(
            models.UserVideoTask.user_id == current_user.id,
            models.UserVideoTask.status == "completed",
            models.UserVideoTask.completed_at >= today_start
        )
    )
    tasks_completed_today = daily_count_result.scalar() or 0
    
    # Fetch plan details to get limit
    plan_result = await db.execute(select(models.Plan).filter(models.Plan.id == current_user.current_plan_id))
    plan = plan_result.scalar_one_or_none()
    
    if plan and tasks_completed_today >= plan.daily_tasks_limit:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Daily task limit reached for your {plan.name} plan ({plan.daily_tasks_limit} tasks).")

    # 3. Process Task Completion
    vt_result = await db.execute(select(models.VideoTask).filter(models.VideoTask.id == task_completion.video_task_id))
    video_task = vt_result.scalar_one_or_none()
    
    if not video_task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video task not found")

    # Check if user has already completed this specific task (regardless of daily limit)
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

    # Update withdrawal wallet balance with task reward
    if hasattr(current_user, "withdrawal_wallet_balance"):
        current_balance = getattr(current_user, "withdrawal_wallet_balance", 0.0) or 0.0
        setattr(current_user, "withdrawal_wallet_balance", current_balance + video_task.reward_amount)
    
    # --- Multi-Tier Referral Rebates ---
    # Tier A: 10%, Tier B: 4%, Tier C: 1%
    rebate_config = [("A", 0.10), ("B", 0.04), ("C", 0.01)]
    
    # Fetch referrer from the new ReferralRelationship table
    rel_result = await db.execute(select(models.ReferralRelationship).filter(models.ReferralRelationship.user_id == current_user.id))
    rel = rel_result.scalar_one_or_none()
    current_referrer_id = rel.referrer_id if rel else None
    
    for tier_label, percentage in rebate_config:
        if not current_referrer_id:
            break
            
        referrer_result = await db.execute(select(models.User).filter(models.User.id == current_referrer_id))
        referrer = referrer_result.scalar_one_or_none()
        
        if referrer:
            rebate_amount = video_task.reward_amount * percentage
            
            # 1. Update referrer's withdrawal wallet
            if hasattr(referrer, "withdrawal_wallet_balance"):
                ref_balance = getattr(referrer, "withdrawal_wallet_balance", 0.0) or 0.0
                setattr(referrer, "withdrawal_wallet_balance", ref_balance + rebate_amount)
            
            # 2. Update referral code stats
            code_result = await db.execute(select(models.ReferralCode).filter(models.ReferralCode.user_id == referrer.id).limit(1))
            ref_code = code_result.scalar_one_or_none()
            if ref_code:
                current_rebate_total = getattr(ref_code, "task_rebate_amount", 0.0) or 0.0
                setattr(ref_code, "task_rebate_amount", current_rebate_total + rebate_amount)
            
            # Move up the chain using the relationship table
            next_rel_result = await db.execute(select(models.ReferralRelationship).filter(models.ReferralRelationship.user_id == referrer.id))
            next_rel = next_rel_result.scalar_one_or_none()
            current_referrer_id = next_rel.referrer_id if next_rel else None
        else:
            break
    
    await db.commit()
    await db.refresh(current_user)
    await db.refresh(user_video_task)

    return {"message": "Task completed successfully, wallet updated, and referral rebates distributed"}

# --- Existing Endpoints ---

class DashboardSummary(BaseModel):
    footage_labeled_min: int
    approved_roles: str
    certifications_earned: int
    active_tasks: int
    completed_tasks: int
    pending_videos: int
    recent_activity: List[dict]
    earnings_history: List[dict]

class LearningHubContent(BaseModel):
    guidelines: str
    references: str
    training_videos: str

@router.get("/dashboard/summary", response_model=DashboardSummary)
async def get_dashboard_summary(
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    try:
        # Count certifications
        try:
            cert_result = await db.execute(
                select(func.count(models.UserCertification.id)).filter(
                    models.UserCertification.user_id == current_user.id,
                    models.UserCertification.status == "completed"
                )
            )
            completed_certs = cert_result.scalar() or 0
        except Exception as e:
            print(f"Error counting certifications: {e}")
            completed_certs = 0
        
        # Count active (available) tasks
        all_tasks = []
        try:
            all_tasks_result = await db.execute(select(models.VideoTask))
            all_tasks = all_tasks_result.scalars().all() or []
        except Exception as e:
            print(f"Error fetching tasks: {e}")
            all_tasks = []
        
        # Get user tasks
        user_tasks = {}
        try:
            uvt_result = await db.execute(
                select(models.UserVideoTask).filter(
                    models.UserVideoTask.user_id == current_user.id
                )
            )
            user_tasks = {uvt.video_task_id: uvt.status for uvt in (uvt_result.scalars().all() or [])}
        except Exception as e:
            print(f"Error fetching user tasks: {e}")
            user_tasks = {}
        
        completed_tasks_count = sum(1 for status in user_tasks.values() if status == "completed")
        active_tasks_count = max(0, len(all_tasks) - completed_tasks_count)
        pending_videos_count = sum(1 for status in user_tasks.values() if status == "pending")

        # Get recent activity
        recent_activity = []
        try:
            recent_uvt_result = await db.execute(
                select(models.UserVideoTask, models.VideoTask)
                .join(models.VideoTask)
                .filter(models.UserVideoTask.user_id == current_user.id)
                .order_by(models.UserVideoTask.completed_at.desc())
                .limit(5)
            )
            
            for uvt, vt in recent_uvt_result.all():
                try:
                    recent_activity.append({
                        "id": uvt.id,
                        "description": f"Completed: {vt.title}",
                        "amount": f"+ ${vt.reward_amount:.2f}",
                        "status": uvt.status.capitalize()
                    })
                except Exception as e:
                    print(f"Error processing activity: {e}")
                    continue
        except Exception as e:
            print(f"Error fetching recent activity: {e}")
            recent_activity = []
        
        # Get earnings history for the last 7 days (including today)
        earnings_history = []
        days_map = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        today = datetime.now(timezone.utc)
        
        for i in range(6, -1, -1):
            target_day = today - timedelta(days=i)
            day_start = target_day.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day_start + timedelta(days=1)
            
            # Sum rewards for tasks completed on that day
            query = (
                select(func.sum(models.VideoTask.reward_amount))
                .join(models.UserVideoTask, models.VideoTask.id == models.UserVideoTask.video_task_id)
                .filter(
                    models.UserVideoTask.user_id == current_user.id,
                    models.UserVideoTask.status == "completed",
                    models.UserVideoTask.completed_at >= day_start,
                    models.UserVideoTask.completed_at < day_end
                )
            )
            earnings_result = await db.execute(query)
            daily_sum = earnings_result.scalar() or 0.0
            
            earnings_history.append({"day": days_map[day_start.weekday()], "value": float(daily_sum)})

        return {
            "footage_labeled_min": 0,
            "approved_roles": "None yet",
            "certifications_earned": completed_certs,
            "active_tasks": active_tasks_count,
            "completed_tasks": completed_tasks_count,
            "pending_videos": pending_videos_count,
            "recent_activity": recent_activity,
            "earnings_history": earnings_history
        }
    except Exception as e:
        print(f"Fatal error in dashboard summary: {e}")
        # Return safe defaults
        return {
            "footage_labeled_min": 0,
            "approved_roles": "None yet",
            "certifications_earned": 0,
            "active_tasks": 0,
            "completed_tasks": 0,
            "pending_videos": 0,
            "recent_activity": [],
            "earnings_history": []
        }

@router.get("/training/certifications", response_model=List[CertificationSchema])
async def get_certifications(
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    try:
        result = await db.execute(select(models.Certification))
        all_certs = result.scalars().all()
        
        uc_result = await db.execute(
            select(models.UserCertification).filter(models.UserCertification.user_id == current_user.id)
        )
        user_certs = {uc.certification_id: uc.status for uc in uc_result.scalars().all()}
        
        # Fetch a fallback video from video_tasks if needed
        fallback_video_url = "https://www.youtube.com/embed/dQw4w9WgXcQ" # Default fallback
        try:
            video_result = await db.execute(select(models.VideoTask).limit(1))
            fallback_video = video_result.scalars().first()
            if fallback_video and hasattr(fallback_video, 'video_url'):
                fallback_video_url = fallback_video.video_url
        except Exception as e:
            print(f"Error fetching fallback video: {e}")

        response = []
        for cert in all_certs:
            status = user_certs.get(cert.id, "available")
            response.append({
                "id": cert.id, 
                "name": cert.name, 
                "description": cert.description or "",
                "estimated_time": cert.estimated_time or "5 min",
                "video_url": (cert.video_url if hasattr(cert, 'video_url') and cert.video_url else fallback_video_url),
                "status": status
            })
        
        return response
    except Exception as e:
        print(f"Error in get_certifications: {e}")
        return []

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
    user_cert = uc_result.scalars().first()
    
    if user_cert:
        # If already completed or in progress, just return success so the UI can proceed
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

@router.post("/training/certifications/{id}/complete", response_model=dict)
async def complete_certification(
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
    
    if not user_cert:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User certification not found")
    
    user_cert.status = "completed"
    user_cert.completed_at = datetime.now(timezone.utc)
    
    # Update user's global trained status
    current_user.is_trained = True
    
    await db.commit()
    await db.refresh(current_user)
    
    return {"message": "Certification completed"}

@router.get("/training/certificate")
async def get_certificate(
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    if not current_user.is_trained:
        raise HTTPException(status_code=400, detail="Training not completed")
    
    # Create PDF in memory
    pdf = FPDF(orientation='L', unit='mm', format='A4')
    pdf.add_page()
    
    # Set margins
    pdf.set_margins(0, 0, 0)
    
    # Background Color (Light subtle cream/blue)
    pdf.set_fill_color(250, 251, 255)
    pdf.rect(0, 0, 297, 210, 'F')
    
    # Decorative Border
    pdf.set_draw_color(89, 50, 234) # Purple color from UI
    pdf.set_line_width(2)
    pdf.rect(10, 10, 277, 190)
    pdf.set_line_width(0.5)
    pdf.rect(13, 13, 271, 184)
    
    # Logo / Brand
    pdf.set_font('Arial', 'B', 24)
    pdf.set_text_color(15, 23, 42)
    pdf.set_xy(0, 30)
    pdf.cell(297, 10, 'AdPulseAI', align='C')
    
    # Certificate Title
    pdf.set_font('Arial', 'B', 40)
    pdf.set_text_color(89, 50, 234)
    pdf.set_xy(0, 60)
    pdf.cell(297, 20, 'CERTIFICATE OF COMPLETION', align='C')
    
    # Subtitle
    pdf.set_font('Arial', '', 16)
    pdf.set_text_color(100, 116, 139)
    pdf.set_xy(0, 85)
    pdf.cell(297, 10, 'This is to certify that', align='C')
    
    # User Name
    user_name = f"{current_user.first_name} {current_user.last_name}"
    pdf.set_font('Arial', 'B', 32)
    pdf.set_text_color(15, 23, 42)
    pdf.set_xy(0, 105)
    pdf.cell(297, 15, user_name.upper(), align='C')
    
    # Divider line under name
    pdf.set_draw_color(226, 232, 240)
    pdf.line(80, 125, 217, 125)
    
    # Achievement text
    pdf.set_font('Arial', '', 16)
    pdf.set_text_color(71, 85, 105)
    pdf.set_xy(0, 135)
    pdf.multi_cell(297, 8, 'has successfully completed the professional training program for\nVIDEO REVIEWING MASTERY', align='C')
    
    # Date and Signature Section
    completion_date = datetime.now().strftime("%B %d, %Y")
    
    # Date
    pdf.set_font('Arial', 'B', 12)
    pdf.set_text_color(15, 23, 42)
    pdf.set_xy(60, 170)
    pdf.cell(50, 5, completion_date, align='C')
    pdf.set_font('Arial', '', 10)
    pdf.set_text_color(100, 116, 139)
    pdf.set_xy(60, 175)
    pdf.cell(50, 5, 'Date of Achievement', align='C')
    pdf.line(60, 168, 110, 168)
    
    # Signature
    pdf.set_font('Arial', 'B', 12)
    pdf.set_text_color(15, 23, 42)
    pdf.set_xy(187, 170)
    pdf.cell(50, 5, 'AdPulseAI Certification Board', align='C')
    pdf.set_font('Arial', '', 10)
    pdf.set_text_color(100, 116, 139)
    pdf.set_xy(187, 175)
    pdf.cell(50, 5, 'Authorized Signature', align='C')
    pdf.line(187, 168, 237, 168)
    
    # Badge / Seal (Simple geometric representation)
    pdf.set_fill_color(89, 50, 234)
    pdf.circle(148.5, 175, 15, 'F')
    pdf.set_text_color(255, 255, 255)
    pdf.set_font('Arial', 'B', 8)
    pdf.set_xy(138.5, 172)
    pdf.cell(20, 5, 'OFFICIAL', align='C')
    pdf.set_xy(138.5, 177)
    pdf.cell(20, 5, 'VERIFIED', align='C')
    
    # Output PDF
    pdf_output = pdf.output(dest='S')
    
    return Response(
        content=pdf_output,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=AdPulseAI_Certificate_{current_user.last_name}.pdf"}
    )
