from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class PlanBase(BaseModel):
    name: str
    price: float
    daily_tasks_limit: int
    validity_days: int
    description: Optional[str] = None
    is_active: bool = True
    is_upgrade_only: bool = False

class PlanCreate(PlanBase):
    pass

class Plan(PlanBase):
    id: int

    class Config:
        from_attributes = True

class UserPlanHistoryBase(BaseModel):
    user_id: int
    plan_id: int
    purchase_price: float
    purchased_at: datetime
    expires_at: datetime
    status: str
    refunded_amount: float

class UserPlanHistoryCreate(UserPlanHistoryBase):
    pass

class UserPlanHistory(UserPlanHistoryBase):
    id: int

    class Config:
        from_attributes = True


class UpgradeRefundResponse(BaseModel):
    """
    Response schema for upgrade refund records.
    Exposes lock status so the frontend can display pending vs released refunds.
    """
    id: int
    amount: float
    status: str              # 'pending' (locked) or 'released' (cashable)
    created_at: Optional[datetime]
    release_at: Optional[datetime]   # When the 72-hour lock expires
    released_at: Optional[datetime]  # Actual release timestamp (null if still pending)
    seconds_until_release: int       # Countdown in seconds (0 if released/due)

    class Config:
        from_attributes = True
