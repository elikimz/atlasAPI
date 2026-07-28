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
from app.services.cache import CacheKeys, CacheTTL, cache, invalidate_shared_cache, invalidate_user_cache

router = APIRouter()

# --- Task Endpoints ---

@router.get("/tasks/available")
async def get_available_tasks(
    db: AsyncSession = Depends(get_async_db),
    current_user: models.User = Depends(get_current_user),
):
    """Return a cached, user-scoped task queue with completed work excluded."""
    async def load_available_tasks() -> list[dict]:
        # Task Loading Logic:
        # 1. If Intern (plan_id=1), show ONLY tasks where plan_id=1.
        # 2. For other levels, show tasks where plan_id is NULL (global) OR plan_id matches their level.
        if current_user.current_plan_id == 1:
            task_filter = (models.VideoTask.plan_id == 1)
        else:
            task_filter = (models.VideoTask.plan_id.is_(None)) | (models.VideoTask.plan_id == current_user.current_plan_id)

        query = select(models.VideoTask).outerjoin(
            models.UserVideoTask,
            (models.UserVideoTask.video_task_id == models.VideoTask.id)
            & (models.UserVideoTask.user_id == current_user.id),
        ).filter(
            task_filter | (models.UserVideoTask.id.is_not(None))
        )
        result = await db.execute(query)
        visible_tasks = result.scalars().all()

        uvt_result = await db.execute(
            select(models.UserVideoTask).filter(models.UserVideoTask.user_id == current_user.id)
        )
        user_tasks = {uvt.video_task_id: uvt.status for uvt in uvt_result.scalars().all()}
        return [
            {
                "id": task.id,
                "title": task.title,
                "description": task.description,
                "video_url": task.video_url,
                "reward_amount": task.reward_amount,
                "status": user_tasks.get(task.id, "available"),
            }
            for task in visible_tasks
            if user_tasks.get(task.id, "available") != "completed"
        ]

    return await cache.get_or_set(
        CacheKeys.user_available_tasks(current_user.id),
        CacheTTL.TASKS,
        load_available_tasks,
    )


@router.get("/tasks/all", response_model=List[AvailableTask])
async def get_all_tasks(
    db: AsyncSession = Depends(get_async_db),
    current_user: models.User = Depends(get_current_user),
):
    """Return all playable tasks using the same user-scoped cache namespace."""
    async def load_all_tasks() -> list[dict]:
        # Task Loading Logic (All Tasks):
        if current_user.current_plan_id == 1:
            task_filter = (models.VideoTask.plan_id == 1)
        else:
            task_filter = (models.VideoTask.plan_id.is_(None)) | (models.VideoTask.plan_id == current_user.current_plan_id)

        query = select(models.VideoTask).outerjoin(
            models.UserVideoTask,
            (models.UserVideoTask.video_task_id == models.VideoTask.id)
            & (models.UserVideoTask.user_id == current_user.id),
        ).filter(
            task_filter | (models.UserVideoTask.id.is_not(None))
        )
        result = await db.execute(query)
        return [
            {
                "id": task.id,
                "title": task.title,
                "description": task.description,
                "video_url": task.video_url,
                "reward_amount": task.reward_amount,
            }
            for task in result.scalars().all()
        ]

    return await cache.get_or_set(
        CacheKeys.user_all_tasks(current_user.id),
        CacheTTL.TASKS,
        load_all_tasks,
    )


@router.post("/tasks/complete", status_code=status.HTTP_200_OK)
async def complete_task(
    task_completion: UserTaskCompletion,
    db: AsyncSession = Depends(get_async_db),
    current_user: models.User = Depends(get_current_user)
):
    """
    Complete a task and distribute rewards.

    Rule 1 — User's Own Task Earnings:
      Credit the task reward_amount directly to the user's withdrawal_wallet_balance.
      This amount is counted in Task Earnings → Total Earnings.

    Rule 2 — Multi-Tier Task Rebates:
      Walk up the referral chain (up to 3 tiers) and credit flat rebate amounts
      to each upline's withdrawal_wallet_balance and referral code stats.
      Tier A: $0.01, Tier B: $0.005, Tier C: $0.0025
    """
    # 1. Check Plan Validity
    if not current_user.current_plan_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No active plan found. Please purchase a plan to start earning."
        )

    if current_user.plan_expiry_date and _as_utc(current_user.plan_expiry_date) < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your plan has expired. Please upgrade to a higher tier to continue earning."
        )

    # 2. Check Daily Task Limit
    # Count only tasks completed today AND after the current plan was activated.
    # This ensures that tasks completed on a previous (lower-tier) plan today do
    # NOT count against the new plan's higher daily limit after an upgrade.
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    plan_start = _as_utc(current_user.plan_start_date) or today_start
    # The effective window starts at whichever is later: today's midnight or plan activation time.
    count_since = max(today_start, plan_start)

    daily_count_result = await db.execute(
        select(func.count(models.UserVideoTask.id))
        .filter(
            models.UserVideoTask.user_id == current_user.id,
            models.UserVideoTask.status == "completed",
            models.UserVideoTask.completed_at >= count_since
        )
    )
    tasks_completed_today = daily_count_result.scalar() or 0

    plan_result = await db.execute(
        select(models.Plan).filter(models.Plan.id == current_user.current_plan_id)
    )
    plan = plan_result.scalar_one_or_none()

    if plan and tasks_completed_today >= plan.daily_tasks_limit:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Daily task limit reached for your {plan.name} plan ({plan.daily_tasks_limit} tasks). Upgrade to a higher plan to complete more tasks today."
        )

    # 3. Verify task belongs to user's plan
    query = select(models.VideoTask).outerjoin(
        models.UserVideoTask,
        (models.UserVideoTask.video_task_id == models.VideoTask.id) & (models.UserVideoTask.user_id == current_user.id)
    ).filter(
        models.VideoTask.id == task_completion.video_task_id,
        (models.VideoTask.plan_id == current_user.current_plan_id) | (models.UserVideoTask.id != None)
    )

    vt_result = await db.execute(query)
    video_task = vt_result.scalar_one_or_none()

    if not video_task:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This task is not available for your current plan."
        )

    # 4. Check if already completed
    uvt_result = await db.execute(
        select(models.UserVideoTask).filter(
            models.UserVideoTask.user_id == current_user.id,
            models.UserVideoTask.video_task_id == task_completion.video_task_id
        )
    )
    user_video_task = uvt_result.scalar_one_or_none()

    if user_video_task and user_video_task.status == "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Task already completed by user"
        )

    if not user_video_task:
        user_video_task = models.UserVideoTask(
            user_id=current_user.id,
            video_task_id=video_task.id,
            status="completed",
            completed_at=datetime.now(timezone.utc)
        )
        db.add(user_video_task)
    else:
        user_video_task.status = "completed"
        user_video_task.completed_at = datetime.now(timezone.utc)

    # ─────────────────────────────────────────────────────────────────────────
    # Rule 1: Credit task reward to user's withdrawal wallet (cashable earnings)
    # This is the user's own Task Earnings component of Total Earnings.
    # ─────────────────────────────────────────────────────────────────────────
    current_user.withdrawal_wallet_balance = (
        current_user.withdrawal_wallet_balance or 0.0
    ) + video_task.reward_amount

    # Log to EarningsLog for GMT-based period calculations
    db.add(models.EarningsLog(
        user_id=current_user.id,
        amount=video_task.reward_amount,
        type="task_reward",
        description=f"Task reward: {video_task.title}"
    ))

    # ─────────────────────────────────────────────────────────────────────────
    # Rule 2: Multi-Tier Task Rebates
    # Walk up the referral chain and credit flat rebate amounts to each upline.
    # Tier A (direct referrer): $0.01
    # Tier B (referrer's referrer): $0.005
    # Tier C (3rd level up): $0.0025
    # ─────────────────────────────────────────────────────────────────────────
    rebate_config = [
        ("tier_a_task_rebate", 0.01),
        ("tier_b_task_rebate", 0.005),
        ("tier_c_task_rebate", 0.0025),
    ]

    rel_result = await db.execute(
        select(models.ReferralRelationship).filter(
            models.ReferralRelationship.user_id == current_user.id
        )
    )
    rel = rel_result.scalar_one_or_none()
    current_referrer_id = rel.referrer_id if rel else None
    affected_referrer_ids: list[int] = []

    for field_name, flat_amount in rebate_config:
        if not current_referrer_id:
            break

        referrer_result = await db.execute(
            select(models.User).filter(models.User.id == current_referrer_id)
        )
        referrer = referrer_result.scalar_one_or_none()

        if referrer:
            affected_referrer_ids.append(referrer.id)
            # Credit rebate to referrer's withdrawal wallet (cashable earnings)
            referrer.withdrawal_wallet_balance = (
                referrer.withdrawal_wallet_balance or 0.0
            ) + flat_amount

            # Log to EarningsLog for GMT-based period calculations
            db.add(models.EarningsLog(
                user_id=referrer.id,
                amount=flat_amount,
                type="task_rebate",
                description=f"Task rebate from downline {current_user.email} (Tier {field_name.split('_')[1].upper()})"
            ))

            # Update referral code stats
            code_result = await db.execute(
                select(models.ReferralCode)
                .filter(models.ReferralCode.user_id == referrer.id)
                .limit(1)
            )
            ref_code = code_result.scalar_one_or_none()
            if ref_code:
                current_val = getattr(ref_code, field_name, 0.0) or 0.0
                setattr(ref_code, field_name, current_val + flat_amount)
                ref_code.task_rebate_amount = (ref_code.task_rebate_amount or 0.0) + flat_amount

            # Move up the chain
            next_rel_result = await db.execute(
                select(models.ReferralRelationship).filter(
                    models.ReferralRelationship.user_id == referrer.id
                )
            )
            next_rel = next_rel_result.scalar_one_or_none()
            current_referrer_id = next_rel.referrer_id if next_rel else None
        else:
            break

    await db.commit()
    await db.refresh(current_user)
    await db.refresh(user_video_task)

    # Only invalidate after the transaction is durable. Each upline receives a
    # wallet and referral-code change from the same completion event.
    await invalidate_user_cache(current_user.id, "tasks", "dashboard", "referrals", "payments")
    for referrer_id in affected_referrer_ids:
        await invalidate_user_cache(referrer_id, "dashboard", "referrals", "payments")
    await invalidate_shared_cache("admin_stats")

    return {
        "message": "Task completed successfully. Earnings credited and referral rebates distributed.",
        "reward_amount": video_task.reward_amount,
    }


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# --- Dashboard ---

class DashboardSummary(BaseModel):
    footage_labeled_min: float
    today_earnings: float
    this_week_earnings: float
    this_month_earnings: float
    approved_roles: str
    certifications_earned: int
    active_tasks: int
    completed_tasks: int
    pending_videos: int
    recent_activity: List[dict]
    earnings_history: List[dict]
    total_tasks_completed: int
    # ── Earnings breakdown (all exclude recharge/deposit amounts) ──
    total_earnings: float           # Sum of all profit-generating activities
    task_earnings: float            # User's own completed task rewards
    referral_commission: float      # Multi-tier invite commissions (first-purchase only)
    task_rebate_commission: float   # Multi-tier task rebates from downline activity
    bonus_refunded: float           # Released upgrade refunds (immediate release)
    pending_refund: float           # Legacy upgrade refunds still pending (should be 0 for new upgrades)


class LearningHubContent(BaseModel):
    guidelines: str
    references: str
    training_videos: str


async def _build_dashboard_summary(
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    """
    Dashboard summary with correct wallet calculation rules:

    Deposit Wallet = Total Funds Recharged − Funds Spent on Plan Purchases/Upgrades
      → This is simply deposit_wallet_balance (managed by admin deposit approval
        and plan purchase/upgrade deductions). No earnings are ever added here.

    Total Earnings = Task Earnings
                   + Multi-Tier Task Rebates
                   + Multi-Tier Invite Commissions (first-purchase only)
                   + Released Upgrade Refunds (post 72-hour lock)
      → Recharge amounts are NEVER included in Total Earnings.

    Periodic Metrics (Today, This Week, This Month):
      - Strictly based on GMT (00:00:00 boundary).
      - Includes ALL profit-generating events (task_reward, task_rebate,
        invite_commission, upgrade_refund).
      - Excludes recharges/deposits.
    """
    try:
        # Strict GMT boundaries
        now_gmt = datetime.now(timezone.utc)
        today_start_gmt = now_gmt.replace(hour=0, minute=0, second=0, microsecond=0)
        # Week starts Monday 00:00:00 GMT
        week_start_gmt = today_start_gmt - timedelta(days=now_gmt.weekday())
        # Month starts 1st 00:00:00 GMT
        month_start_gmt = today_start_gmt.replace(day=1)

        # ── Helper: sum all qualifying earnings for a given time window ───────
        async def get_total_earnings_sum(start_date: datetime) -> float:
            query = (
                select(func.sum(models.EarningsLog.amount))
                .filter(
                    models.EarningsLog.user_id == current_user.id,
                    models.EarningsLog.created_at >= start_date
                )
            )
            result = await db.execute(query)
            return result.scalar() or 0.0

        today_earnings = await get_total_earnings_sum(today_start_gmt)
        this_week_earnings = await get_total_earnings_sum(week_start_gmt)
        this_month_earnings = await get_total_earnings_sum(month_start_gmt)

        # ── Certifications ────────────────────────────────────────────────────
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

        # ── Task counts ───────────────────────────────────────────────────────
        total_tasks_completed_result = await db.execute(
            select(func.count(models.UserVideoTask.id))
            .filter(
                models.UserVideoTask.user_id == current_user.id,
                models.UserVideoTask.status == "completed"
            )
        )
        total_tasks_completed = total_tasks_completed_result.scalar() or 0
        footage_labeled_min = round((total_tasks_completed * 0.3) / 60, 2)

        user_scoped_tasks = []
        try:
            query = select(models.VideoTask).outerjoin(
                models.UserVideoTask,
                (models.UserVideoTask.video_task_id == models.VideoTask.id) & (models.UserVideoTask.user_id == current_user.id)
            ).filter(
                (models.VideoTask.plan_id == current_user.current_plan_id) | (models.UserVideoTask.id != None)
            )
            all_tasks_result = await db.execute(query)
            user_scoped_tasks = all_tasks_result.scalars().all() or []
        except Exception as e:
            print(f"Error fetching tasks: {e}")

        user_tasks = {}
        try:
            uvt_result = await db.execute(
                select(models.UserVideoTask).filter(models.UserVideoTask.user_id == current_user.id)
            )
            user_tasks = {uvt.video_task_id: uvt.status for uvt in (uvt_result.scalars().all() or [])}
        except Exception as e:
            print(f"Error fetching user tasks: {e}")

        completed_tasks_count = sum(1 for s in user_tasks.values() if s == "completed")
        active_tasks_count = max(0, len(user_scoped_tasks) - completed_tasks_count)
        pending_videos_count = sum(1 for s in user_tasks.values() if s == "pending")

        # ── Recent activity ───────────────────────────────────────────────────
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
        except Exception as e:
            print(f"Error fetching recent activity: {e}")

        # ── Earnings breakdown ────────────────────────────────────────────────
        # Rule: Total Earnings MUST exclude recharge/deposit amounts.
        # It is the sum of profit-generating activities only.

        # 1. Task Earnings: sum of all completed video task rewards
        task_earnings_res = await db.execute(
            select(func.sum(models.VideoTask.reward_amount))
            .join(models.UserVideoTask, models.VideoTask.id == models.UserVideoTask.video_task_id)
            .filter(
                models.UserVideoTask.user_id == current_user.id,
                models.UserVideoTask.status == "completed"
            )
        )
        task_earnings = task_earnings_res.scalar() or 0.0

        # 2. Multi-Tier Invite Commissions (from referral code records)
        ref_code_query = select(models.ReferralCode).filter(
            models.ReferralCode.user_id == current_user.id
        )
        ref_codes_res = await db.execute(ref_code_query)
        ref_codes = ref_codes_res.scalars().all()
        referral_commission = sum(getattr(c, "earned_amount", 0.0) or 0.0 for c in ref_codes)

        # 3. Multi-Tier Task Rebates (from downline task completions)
        task_rebate_commission = sum(getattr(c, "task_rebate_amount", 0.0) or 0.0 for c in ref_codes)

        # 4. Released Upgrade Refunds (post 72-hour lock — these are now cashable)
        #    Only 'released' records count toward Total Earnings.
        #    'pending' records are locked and must NOT be included.
        released_refunds_res = await db.execute(
            select(func.sum(models.UpgradeRefund.amount)).filter(
                models.UpgradeRefund.user_id == current_user.id,
                models.UpgradeRefund.status == "released"
            )
        )
        bonus_refunded = released_refunds_res.scalar() or 0.0

        # Pending (locked) refunds — informational only, not counted in earnings yet
        pending_refunds_res = await db.execute(
            select(func.sum(models.UpgradeRefund.amount)).filter(
                models.UpgradeRefund.user_id == current_user.id,
                models.UpgradeRefund.status == "pending"
            )
        )
        pending_refund = pending_refunds_res.scalar() or 0.0

        # Total Earnings = Task Earnings + Rebates + Invite Commissions + Released Refunds
        # Deposit recharges are NEVER included here.
        total_earnings = task_earnings + referral_commission + task_rebate_commission + bonus_refunded

        # ── Earnings history (last 7 days, task earnings only) ────────────────
        earnings_history = []
        days_map = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        for i in range(6, -1, -1):
            target_day = today_start_gmt - timedelta(days=i)
            day_start = target_day.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day_start + timedelta(days=1)

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
            "footage_labeled_min": footage_labeled_min,
            "today_earnings": today_earnings,
            "this_week_earnings": this_week_earnings,
            "this_month_earnings": this_month_earnings,
            "approved_roles": "None yet",
            "certifications_earned": completed_certs,
            "active_tasks": active_tasks_count,
            "completed_tasks": completed_tasks_count,
            "pending_videos": pending_videos_count,
            "recent_activity": recent_activity,
            "earnings_history": earnings_history,
            "total_tasks_completed": total_tasks_completed,
            "total_earnings": total_earnings,
            "task_earnings": task_earnings,
            "referral_commission": referral_commission,
            "task_rebate_commission": task_rebate_commission,
            "bonus_refunded": bonus_refunded,
            "pending_refund": pending_refund,
        }
    except Exception as e:
        print(f"Fatal error in dashboard summary: {e}")
        return {
            "footage_labeled_min": 0.0,
            "today_earnings": 0.0,
            "this_week_earnings": 0.0,
            "this_month_earnings": 0.0,
            "approved_roles": "None yet",
            "certifications_earned": 0,
            "active_tasks": 0,
            "completed_tasks": 0,
            "pending_videos": 0,
            "recent_activity": [],
            "earnings_history": [],
            "total_tasks_completed": 0,
            "total_earnings": 0.0,
            "task_earnings": 0.0,
            "referral_commission": 0.0,
            "task_rebate_commission": 0.0,
            "bonus_refunded": 0.0,
            "pending_refund": 0.0,
        }


@router.get("/dashboard/summary", response_model=DashboardSummary)
async def get_dashboard_summary(
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Serve an aggregate dashboard response from a short-lived user cache."""
    return await cache.get_or_set(
        CacheKeys.user_dashboard(current_user.id),
        CacheTTL.DASHBOARD,
        lambda: _build_dashboard_summary(current_user=current_user, db=db),
    )


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

        fallback_video_url = "https://www.youtube.com/embed/dQw4w9WgXcQ"
        try:
            video_result = await db.execute(select(models.VideoTask).limit(1))
            fallback_video = video_result.scalars().first()
            if fallback_video and hasattr(fallback_video, 'video_url'):
                fallback_video_url = fallback_video.video_url
        except Exception as e:
            print(f"Error fetching fallback video: {e}")

        response = []
        for cert in all_certs:
            cert_status = "completed" if current_user.is_trained else user_certs.get(cert.id, "available")
            response.append({
                "id": cert.id,
                "name": cert.name,
                "description": cert.description or "",
                "estimated_time": cert.estimated_time or "5 min",
                "video_url": (cert.video_url if hasattr(cert, 'video_url') and cert.video_url else fallback_video_url),
                "status": cert_status
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
    if current_user.is_trained:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Training already completed. Please download your certificate."
        )

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
        if user_cert.status == "completed":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Certification already completed. Please download your certificate."
            )
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

    pdf = FPDF(orientation='L', unit='mm', format='A4')
    pdf.add_page()
    pdf.set_margins(0, 0, 0)

    pdf.set_fill_color(250, 251, 255)
    pdf.rect(0, 0, 297, 210, 'F')

    pdf.set_draw_color(89, 50, 234)
    pdf.set_line_width(2)
    pdf.rect(10, 10, 277, 190)
    pdf.set_line_width(0.5)
    pdf.rect(13, 13, 271, 184)

    pdf.set_font('Arial', 'B', 24)
    pdf.set_text_color(15, 23, 42)
    pdf.set_xy(0, 30)
    pdf.cell(297, 10, 'AdPulseAI', align='C')

    pdf.set_font('Arial', 'B', 40)
    pdf.set_text_color(89, 50, 234)
    pdf.set_xy(0, 60)
    pdf.cell(297, 20, 'CERTIFICATE OF COMPLETION', align='C')

    pdf.set_font('Arial', '', 16)
    pdf.set_text_color(100, 116, 139)
    pdf.set_xy(0, 85)
    pdf.cell(297, 10, 'This is to certify that', align='C')

    first = current_user.first_name or ""
    last = current_user.last_name or ""
    user_name = f"{first} {last}".strip() or current_user.email or "AdPulseAI User"

    pdf.set_font('Arial', 'B', 32)
    pdf.set_text_color(15, 23, 42)
    pdf.set_xy(0, 105)
    pdf.cell(297, 15, user_name.upper(), align='C')

    pdf.set_draw_color(226, 232, 240)
    pdf.line(80, 125, 217, 125)

    pdf.set_font('Arial', '', 16)
    pdf.set_text_color(71, 85, 105)
    pdf.set_xy(0, 135)
    pdf.multi_cell(297, 8, 'has successfully completed the professional training program for\nVIDEO REVIEWING MASTERY', align='C')

    completion_date = datetime.now().strftime("%B %d, %Y")

    pdf.set_font('Arial', 'B', 12)
    pdf.set_text_color(15, 23, 42)
    pdf.set_xy(60, 170)
    pdf.cell(50, 5, completion_date, align='C')
    pdf.set_font('Arial', '', 10)
    pdf.set_text_color(100, 116, 139)
    pdf.set_xy(60, 175)
    pdf.cell(50, 5, 'Date of Achievement', align='C')
    pdf.line(60, 168, 110, 168)

    pdf.set_font('Arial', 'B', 12)
    pdf.set_text_color(15, 23, 42)
    pdf.set_xy(187, 170)
    pdf.cell(50, 5, 'AdPulseAI Certification Board', align='C')
    pdf.set_font('Arial', '', 10)
    pdf.set_text_color(100, 116, 139)
    pdf.set_xy(187, 175)
    pdf.cell(50, 5, 'Authorized Signature', align='C')
    pdf.line(187, 168, 237, 168)

    try:
        pdf.set_fill_color(89, 50, 234)
        pdf.ellipse(148.5 - 15, 175 - 15, 30, 30, 'F')
        pdf.set_text_color(255, 255, 255)
        pdf.set_font('Arial', 'B', 8)
        pdf.set_xy(138.5, 172)
        pdf.cell(20, 5, 'OFFICIAL', align='C')
        pdf.set_xy(138.5, 177)
        pdf.cell(20, 5, 'VERIFIED', align='C')
    except Exception as e:
        print(f"Drawing seal failed: {e}")

    try:
        try:
            pdf_output = pdf.output(dest='S')
        except (TypeError, Exception):
            pdf_output = pdf.output()

        if isinstance(pdf_output, str):
            pdf_output = pdf_output.encode('latin-1')
        elif isinstance(pdf_output, bytearray):
            pdf_output = bytes(pdf_output)

    except Exception as e:
        print(f"PDF output failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate PDF")

    safe_last_name = (current_user.last_name or "User").replace(" ", "_")
    return Response(
        content=pdf_output,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=AdPulseAI_Certificate_{safe_last_name}.pdf",
            "Access-Control-Expose-Headers": "Content-Disposition"
        }
    )
