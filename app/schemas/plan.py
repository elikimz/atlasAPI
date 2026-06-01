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
