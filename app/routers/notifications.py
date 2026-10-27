from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select, func, desc, delete, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from datetime import datetime, timezone

from app.database.database import get_async_db
from app.models import models
from app.routers.auth import get_current_admin_user, get_current_user
from app.services.cache import CacheKeys, CacheTTL, cache, invalidate_user_cache

router = APIRouter()

# --- Schemas ---
class NotificationSchema(BaseModel):
    id: int
    user_id: Optional[int] = None
    title: str
    message: str
    type: str
    is_read: bool
    created_at: datetime

    class Config:
        from_attributes = True

class NotificationCreate(BaseModel):
    user_id: Optional[int] = None  # If null, it's a global notification
    title: str
    message: str
    type: str = "info"

class NotificationMarkRead(BaseModel):
    notification_ids: List[int]

# --- Endpoints ---

@router.get("/notifications", response_model=List[NotificationSchema])
async def get_user_notifications(
    response: Response,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Fetch a user/global notification page through a 20-second cache."""
    async def load_notification_page() -> dict:
        state_join = and_(
            models.UserNotificationState.notification_id == models.Notification.id,
            models.UserNotificationState.user_id == current_user.id,
        )
        visible = or_(
            models.Notification.user_id == current_user.id,
            and_(
                models.Notification.user_id.is_(None),
                or_(
                    models.UserNotificationState.id.is_(None),
                    models.UserNotificationState.dismissed_at.is_(None),
                ),
            ),
        )
        total_result = await db.execute(
            select(func.count(models.Notification.id))
            .outerjoin(models.UserNotificationState, state_join)
            .filter(visible)
        )
        total = total_result.scalar() or 0
        result = await db.execute(
            select(models.Notification, models.UserNotificationState.read_at)
            .outerjoin(models.UserNotificationState, state_join)
            .filter(visible)
            .order_by(desc(models.Notification.created_at), desc(models.Notification.id))
            .offset((page - 1) * limit)
            .limit(limit)
        )
        return {
            "items": [
                {
                    "id": notification.id,
                    "user_id": notification.user_id,
                    "title": notification.title,
                    "message": notification.message,
                    "type": notification.type,
                    "is_read": notification.is_read if notification.user_id == current_user.id else read_at is not None,
                    "created_at": notification.created_at.isoformat() if notification.created_at else None,
                }
                for notification, read_at in result.all()
            ],
            "total": total,
        }

    page_data = await cache.get_or_set(
        CacheKeys.user_notifications(current_user.id, page, limit),
        CacheTTL.NOTIFICATIONS,
        load_notification_page,
    )
    response.headers["X-Total-Count"] = str(page_data["total"])
    response.headers["X-Page"] = str(page)
    response.headers["X-Limit"] = str(limit)
    return page_data["items"]

@router.post("/notifications/mark-read", response_model=dict)
async def mark_notifications_as_read(
    read_request: NotificationMarkRead,
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    """Mark specified notifications as read for the current user."""
    for notif_id in set(read_request.notification_ids):
        notification_query = select(models.Notification).filter(
            models.Notification.id == notif_id,
            (models.Notification.user_id == current_user.id) | (models.Notification.user_id.is_(None)),
        ).with_for_update()
        result = await db.execute(notification_query)
        notification = result.scalar_one_or_none()
        if notification and notification.user_id == current_user.id:
            notification.is_read = True
            db.add(notification)
        elif notification:
            state = await db.scalar(
                select(models.UserNotificationState).where(
                    models.UserNotificationState.user_id == current_user.id,
                    models.UserNotificationState.notification_id == notification.id,
                )
            )
            if state is None:
                db.add(models.UserNotificationState(
                    user_id=current_user.id,
                    notification_id=notification.id,
                    read_at=datetime.now(timezone.utc),
                ))
            elif state.read_at is None:
                state.read_at = datetime.now(timezone.utc)
    
    await db.commit()
    await invalidate_user_cache(current_user.id, "notifications")
    return {"message": "Notifications marked as read."}

@router.delete("/notifications/{notification_id}", response_model=dict)
async def delete_notification(
    notification_id: int,
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    """Delete a specific notification for the current user."""
    notification_query = select(models.Notification).filter(models.Notification.id == notification_id)
    result = await db.execute(notification_query)
    notification = result.scalar_one_or_none()

    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")

    if notification.user_id == current_user.id:
        await db.execute(delete(models.Notification).filter(models.Notification.id == notification_id))
        await db.commit()
        await invalidate_user_cache(current_user.id, "notifications")
        return {"message": "Notification deleted successfully"}
    if notification.user_id is None and (current_user.role == "admin" or current_user.is_admin):
        await db.execute(delete(models.Notification).filter(models.Notification.id == notification_id))
        await db.commit()
        await cache.delete_pattern("adpulseai:user:*:notifications:*")
        return {"message": "Notification deleted successfully"}
    if notification.user_id is None:
        state = await db.scalar(
            select(models.UserNotificationState).where(
                models.UserNotificationState.user_id == current_user.id,
                models.UserNotificationState.notification_id == notification.id,
            )
        )
        now = datetime.now(timezone.utc)
        if state is None:
            state = models.UserNotificationState(
                user_id=current_user.id,
                notification_id=notification.id,
                read_at=now,
                dismissed_at=now,
            )
            db.add(state)
        else:
            state.dismissed_at = now
            state.read_at = state.read_at or now
        await db.commit()
        await invalidate_user_cache(current_user.id, "notifications")
        return {"message": "Notification dismissed for this account"}
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to delete this notification")

@router.delete("/notifications/clear-all", response_model=dict)
async def clear_all_notifications(
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    """Delete all targeted notifications for the current user."""
    from sqlalchemy import delete
    
    # Delete only personal notifications, and dismiss shared notifications for
    # this user without altering notices or read state seen by other members.
    global_ids = (await db.scalars(select(models.Notification.id).where(models.Notification.user_id.is_(None)))).all()
    if global_ids:
        state_rows = (await db.scalars(
            select(models.UserNotificationState).where(
                models.UserNotificationState.user_id == current_user.id,
                models.UserNotificationState.notification_id.in_(global_ids),
            )
        )).all()
        states_by_notification = {row.notification_id: row for row in state_rows}
        now = datetime.now(timezone.utc)
        for notification_id in global_ids:
            state = states_by_notification.get(notification_id)
            if state is None:
                db.add(models.UserNotificationState(
                    user_id=current_user.id,
                    notification_id=notification_id,
                    read_at=now,
                    dismissed_at=now,
                ))
            else:
                state.read_at = state.read_at or now
                state.dismissed_at = now
    await db.execute(delete(models.Notification).filter(models.Notification.user_id == current_user.id))
    await db.commit()
    await invalidate_user_cache(current_user.id, "notifications")

    return {"message": "All personal notifications cleared"}

@router.post("/admin/notifications/send", response_model=NotificationSchema, status_code=status.HTTP_201_CREATED)
async def send_notification(
    notification_data: NotificationCreate,
    current_user: models.User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_async_db)
):
    """Admin endpoint to send a new notification to a specific user or globally."""

    # Validate that user_id (if provided) references an existing user
    if notification_data.user_id is not None:
        user_result = await db.execute(
            select(models.User).filter(models.User.id == notification_data.user_id)
        )
        if user_result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"User with id {notification_data.user_id} does not exist"
            )

    new_notification = models.Notification(
        user_id=notification_data.user_id,
        title=notification_data.title,
        message=notification_data.message,
        type=notification_data.type
    )
    db.add(new_notification)
    await db.commit()
    await db.refresh(new_notification)
    if new_notification.user_id is None:
        await cache.delete_pattern("adpulseai:user:*:notifications:*")
    else:
        await invalidate_user_cache(new_notification.user_id, "notifications")
    return new_notification
