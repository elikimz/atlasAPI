from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import and_, delete, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import get_async_db
from app.models import models
from app.routers.auth import get_current_admin_user, get_current_user
from app.services.cache import (
    CacheKeys,
    CacheTTL,
    cache,
    invalidate_all_notification_cache,
    invalidate_user_cache,
)

router = APIRouter()

_ALLOWED_TYPES = {"info", "success", "warning", "error"}


class NotificationSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: Optional[int] = None
    title: str
    message: str
    type: str
    is_read: bool
    created_at: datetime


class NotificationCreate(BaseModel):
    user_id: Optional[int] = None
    title: str = Field(..., min_length=1, max_length=120)
    message: str = Field(..., min_length=1, max_length=2000)
    type: str = Field(default="info", min_length=1, max_length=20)

    @field_validator("title", "message", "type")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Notification text cannot be empty")
        return value

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: str) -> str:
        value = value.lower()
        if value not in _ALLOWED_TYPES:
            raise ValueError(f"Notification type must be one of: {', '.join(sorted(_ALLOWED_TYPES))}")
        return value


class NotificationMarkRead(BaseModel):
    notification_ids: List[int] = Field(default_factory=list, max_length=100)


class NotificationSummary(BaseModel):
    unread_count: int
    total_count: int
    latest: Optional[NotificationSchema] = None


def _state_join(user_id: int):
    return and_(
        models.UserNotificationState.notification_id == models.Notification.id,
        models.UserNotificationState.user_id == user_id,
    )


def _visible_for_user(user_id: int):
    return or_(
        models.Notification.user_id == user_id,
        and_(
            models.Notification.user_id.is_(None),
            or_(
                models.UserNotificationState.id.is_(None),
                models.UserNotificationState.dismissed_at.is_(None),
            ),
        ),
    )


async def _unread_count(db: AsyncSession, user_id: int) -> int:
    personal = await db.scalar(
        select(func.count(models.Notification.id)).where(
            models.Notification.user_id == user_id,
            models.Notification.is_read.is_(False),
        )
    ) or 0
    global_unread = await db.scalar(
        select(func.count(models.Notification.id))
        .outerjoin(models.UserNotificationState, _state_join(user_id))
        .where(
            models.Notification.user_id.is_(None),
            or_(
                models.UserNotificationState.id.is_(None),
                and_(
                    models.UserNotificationState.read_at.is_(None),
                    models.UserNotificationState.dismissed_at.is_(None),
                ),
            ),
        )
    ) or 0
    return int(personal + global_unread)


@router.get("/admin/notifications", response_model=List[NotificationSchema])
async def get_admin_notifications(
    response: Response,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    current_user: models.User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Return the complete notification ledger for the admin console."""
    total = await db.scalar(select(func.count(models.Notification.id))) or 0
    notifications = (await db.scalars(
        select(models.Notification)
        .order_by(desc(models.Notification.created_at), desc(models.Notification.id))
        .offset((page - 1) * limit)
        .limit(limit)
    )).all()
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Page"] = str(page)
    response.headers["X-Limit"] = str(limit)
    response.headers["X-Has-More"] = str(page * limit < total).lower()
    return notifications


@router.get("/notifications/summary", response_model=NotificationSummary)
async def get_notification_summary(
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Return the badge data without loading the full notification feed."""
    async def load_summary() -> dict:
        total = await db.scalar(
            select(func.count(models.Notification.id))
            .outerjoin(models.UserNotificationState, _state_join(current_user.id))
            .where(_visible_for_user(current_user.id))
        ) or 0
        unread = await _unread_count(db, current_user.id)
        latest_row = (await db.execute(
            select(models.Notification, models.UserNotificationState.read_at)
            .outerjoin(models.UserNotificationState, _state_join(current_user.id))
            .where(_visible_for_user(current_user.id))
            .order_by(desc(models.Notification.created_at), desc(models.Notification.id))
            .limit(1)
        )).first()
        latest = None
        if latest_row:
            notification, read_at = latest_row
            latest = {
                "id": notification.id,
                "user_id": notification.user_id,
                "title": notification.title,
                "message": notification.message,
                "type": notification.type,
                "is_read": notification.is_read if notification.user_id == current_user.id else read_at is not None,
                "created_at": notification.created_at.isoformat() if notification.created_at else None,
            }
        return {"unread_count": unread, "total_count": int(total), "latest": latest}

    return await cache.get_or_set(
        CacheKeys.user_notification_summary(current_user.id),
        CacheTTL.NOTIFICATIONS,
        load_summary,
    )


@router.get("/notifications", response_model=List[NotificationSchema])
async def get_user_notifications(
    response: Response,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Return a stable, newest-first notification page for the current member."""
    async def load_notification_page() -> dict:
        state_join = _state_join(current_user.id)
        visible = _visible_for_user(current_user.id)
        total = await db.scalar(
            select(func.count(models.Notification.id))
            .outerjoin(models.UserNotificationState, state_join)
            .where(visible)
        ) or 0
        result = await db.execute(
            select(models.Notification, models.UserNotificationState.read_at)
            .outerjoin(models.UserNotificationState, state_join)
            .where(visible)
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
            "total": int(total),
        }

    page_data = await cache.get_or_set(
        CacheKeys.user_notifications(current_user.id, page, limit),
        CacheTTL.NOTIFICATIONS,
        load_notification_page,
    )
    response.headers["X-Total-Count"] = str(page_data["total"])
    response.headers["X-Page"] = str(page)
    response.headers["X-Limit"] = str(limit)
    response.headers["X-Has-More"] = str(page * limit < page_data["total"]).lower()
    return page_data["items"]


@router.post("/notifications/mark-read", response_model=dict)
async def mark_notifications_as_read(
    read_request: NotificationMarkRead,
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    ids = set(read_request.notification_ids)
    if not ids:
        return {"message": "No notifications selected.", "updated": 0}
    now = datetime.now(timezone.utc)
    updated = 0
    for notification_id in ids:
        notification = await db.scalar(
            select(models.Notification)
            .where(
                models.Notification.id == notification_id,
                (models.Notification.user_id == current_user.id) | models.Notification.user_id.is_(None),
            )
            .with_for_update()
        )
        if notification is None:
            continue
        if notification.user_id == current_user.id:
            if not notification.is_read:
                notification.is_read = True
                updated += 1
            continue
        state = await db.scalar(select(models.UserNotificationState).where(
            models.UserNotificationState.user_id == current_user.id,
            models.UserNotificationState.notification_id == notification.id,
        ).with_for_update())
        if state is None:
            db.add(models.UserNotificationState(user_id=current_user.id, notification_id=notification.id, read_at=now))
            updated += 1
        elif state.read_at is None:
            state.read_at = now
            updated += 1
    await db.commit()
    await invalidate_user_cache(current_user.id, "notifications")
    return {"message": "Notifications marked as read.", "updated": updated}


@router.post("/notifications/mark-all-read", response_model=dict)
async def mark_all_notifications_as_read(
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    notifications = (await db.execute(
        select(models.Notification.id)
        .outerjoin(models.UserNotificationState, _state_join(current_user.id))
        .where(_visible_for_user(current_user.id))
    )).scalars().all()
    if notifications:
        # Reuse the same scoped logic so global notices remain member-specific.
        for notification_id in notifications:
            notification = await db.scalar(select(models.Notification).where(models.Notification.id == notification_id))
            if notification is None:
                continue
            if notification.user_id == current_user.id:
                notification.is_read = True
            else:
                state = await db.scalar(select(models.UserNotificationState).where(
                    models.UserNotificationState.user_id == current_user.id,
                    models.UserNotificationState.notification_id == notification.id,
                ))
                if state is None:
                    db.add(models.UserNotificationState(user_id=current_user.id, notification_id=notification.id, read_at=datetime.now(timezone.utc)))
                else:
                    state.read_at = state.read_at or datetime.now(timezone.utc)
    await db.commit()
    await invalidate_user_cache(current_user.id, "notifications")
    return {"message": "All visible notifications marked as read.", "updated": len(notifications)}


@router.delete("/notifications/{notification_id}", response_model=dict)
async def delete_notification(
    notification_id: int,
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    notification = await db.scalar(select(models.Notification).where(models.Notification.id == notification_id))
    if notification is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    if notification.user_id == current_user.id:
        await db.execute(delete(models.Notification).where(models.Notification.id == notification_id))
        await db.commit()
        await invalidate_user_cache(current_user.id, "notifications")
        return {"message": "Notification deleted successfully"}
    if notification.user_id is None and (current_user.role == "admin" or current_user.is_admin):
        await db.execute(delete(models.Notification).where(models.Notification.id == notification_id))
        await db.commit()
        await invalidate_all_notification_cache()
        return {"message": "Notification deleted successfully"}
    if notification.user_id is None:
        state = await db.scalar(select(models.UserNotificationState).where(
            models.UserNotificationState.user_id == current_user.id,
            models.UserNotificationState.notification_id == notification.id,
        ).with_for_update())
        now = datetime.now(timezone.utc)
        if state is None:
            db.add(models.UserNotificationState(user_id=current_user.id, notification_id=notification.id, read_at=now, dismissed_at=now))
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
    db: AsyncSession = Depends(get_async_db),
):
    global_ids = (await db.scalars(select(models.Notification.id).where(models.Notification.user_id.is_(None)))).all()
    now = datetime.now(timezone.utc)
    for notification_id in global_ids:
        state = await db.scalar(select(models.UserNotificationState).where(
            models.UserNotificationState.user_id == current_user.id,
            models.UserNotificationState.notification_id == notification_id,
        ))
        if state is None:
            db.add(models.UserNotificationState(user_id=current_user.id, notification_id=notification_id, read_at=now, dismissed_at=now))
        else:
            state.read_at = state.read_at or now
            state.dismissed_at = now
    deleted = await db.execute(delete(models.Notification).where(models.Notification.user_id == current_user.id))
    await db.commit()
    await invalidate_user_cache(current_user.id, "notifications")
    return {"message": "All personal notifications cleared", "deleted": deleted.rowcount or 0}


@router.post("/admin/notifications/send", response_model=NotificationSchema, status_code=status.HTTP_201_CREATED)
async def send_notification(
    notification_data: NotificationCreate,
    current_user: models.User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_async_db),
):
    if notification_data.user_id is not None:
        exists = await db.scalar(select(func.count(models.User.id)).where(models.User.id == notification_data.user_id))
        if not exists:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"User with id {notification_data.user_id} does not exist")
    new_notification = models.Notification(
        user_id=notification_data.user_id,
        title=notification_data.title,
        message=notification_data.message,
        type=notification_data.type,
        is_read=False,
    )
    db.add(new_notification)
    await db.commit()
    await db.refresh(new_notification)
    if new_notification.user_id is None:
        await invalidate_all_notification_cache()
    else:
        await invalidate_user_cache(new_notification.user_id, "notifications")
    return new_notification
