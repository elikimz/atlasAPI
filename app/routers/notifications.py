from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func, desc, delete
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from datetime import datetime

from app.database.database import get_async_db
from app.models import models
from app.routers.auth import get_current_user

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
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    """Fetch all notifications for the current user, including global ones."""
    notifications_query = select(models.Notification).filter(
        (models.Notification.user_id == current_user.id) | (models.Notification.user_id.is_(None))
    ).order_by(desc(models.Notification.created_at))
    
    result = await db.execute(notifications_query)
    notifications = result.scalars().all()
    return notifications

@router.post("/notifications/mark-read", response_model=dict)
async def mark_notifications_as_read(
    read_request: NotificationMarkRead,
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    """Mark specified notifications as read for the current user."""
    for notif_id in read_request.notification_ids:
        notification_query = select(models.Notification).filter(
            models.Notification.id == notif_id,
            (models.Notification.user_id == current_user.id) | (models.Notification.user_id.is_(None))
        )
        result = await db.execute(notification_query)
        notification = result.scalar_one_or_none()
        
        if notification:
            notification.is_read = True
            db.add(notification)
    
    await db.commit()
    return {"message": "Notifications marked as read."}

@router.delete("/notifications/{notification_id}", response_model=dict)
async def delete_notification(
    notification_id: int,
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    """Delete a specific notification for the current user."""
    # Find the notification
    notification_query = select(models.Notification).filter(
        models.Notification.id == notification_id
    )
    result = await db.execute(notification_query)
    notification = result.scalar_one_or_none()

    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")

    # Allow deletion if it's the user's notification or if it's a global notification and the user is an admin
    if notification.user_id == current_user.id or (notification.user_id is None and current_user.is_admin):
        await db.execute(delete(models.Notification).filter(models.Notification.id == notification_id))
        await db.commit()
        return {"message": "Notification deleted successfully"}
    else:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to delete this notification")

@router.delete("/notifications/clear-all", response_model=dict)
async def clear_all_notifications(
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    """Delete all targeted notifications for the current user."""
    from sqlalchemy import delete
    
    # Delete only targeted notifications belonging to this user
    query = delete(models.Notification).filter(models.Notification.user_id == current_user.id)
    await db.execute(query)
    await db.commit()
    
    return {"message": "All personal notifications cleared"}

@router.post("/admin/notifications/send", response_model=NotificationSchema, status_code=status.HTTP_201_CREATED)
async def send_notification(
    notification_data: NotificationCreate,
    current_user: models.User = Depends(get_current_user), # Admin check will be done here
    db: AsyncSession = Depends(get_async_db)
):
    """Admin endpoint to send a new notification to a specific user or globally."""
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only admins can send notifications.")

    new_notification = models.Notification(
        user_id=notification_data.user_id,
        title=notification_data.title,
        message=notification_data.message,
        type=notification_data.type
    )
    db.add(new_notification)
    await db.commit()
    await db.refresh(new_notification)
    return new_notification
