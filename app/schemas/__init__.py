from typing import Optional
from datetime import datetime
from pydantic import BaseModel

class UserBase(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: str

class UserCreate(UserBase):
    pass

class User(UserBase):
    id: int
    created_at: datetime
    is_admin: bool
    deposit_wallet_balance: float
    withdrawal_wallet_balance: float

    class Config:
        from_attributes = True

class OTPRequest(BaseModel):
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    referral_code: Optional[str] = None

class OTPVerify(BaseModel):
    email: str
    otp_code: str

class Token(BaseModel):
    access_token: str
    token_type: str

class CertificationBase(BaseModel):
    name: str
    description: Optional[str] = None
    estimated_time: Optional[str] = None
    video_url: Optional[str] = None
    steps_count: int = 0
    is_active: bool = True

class CertificationCreate(CertificationBase):
    pass

class Certification(CertificationBase):
    id: int

    class Config:
        from_attributes = True

class UserCertificationBase(BaseModel):
    status: str = "available"

class UserCertificationCreate(UserCertificationBase):
    certification_id: int

class UserCertification(UserCertificationBase):
    id: int
    user_id: int
    certification_id: int
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class TaskBase(BaseModel):
    name: str
    description: Optional[str] = None
    required_certification_id: Optional[int] = None
    status: str = "locked"

class TaskCreate(TaskBase):
    pass

class Task(TaskBase):
    id: int

    class Config:
        from_attributes = True

class ReferralCodeBase(BaseModel):
    code: str
    signups_count: int = 0
    trained_count: int = 0
    earned_amount: float = 0.0

class ReferralCodeCreate(ReferralCodeBase):
    pass

class ReferralCode(ReferralCodeBase):
    id: int
    user_id: int

    class Config:
        from_attributes = True

class PaymentBase(BaseModel):
    amount: float
    period: str
    status: str = "pending"
    payout_date: Optional[datetime] = None

class PaymentCreate(PaymentBase):
    pass

class Payment(PaymentBase):
    id: int
    user_id: int
    created_at: datetime

    class Config:
        from_attributes = True

class EvaluationBase(BaseModel):
    name: str
    episodes_completed: int = 0
    total_episodes_required: int = 5
    episodes_passing_audit: int = 0
    status: str = "in_progress"

class EvaluationCreate(EvaluationBase):
    pass

class Evaluation(EvaluationBase):
    id: int
    user_id: int

    class Config:
        from_attributes = True

class VideoTaskBase(BaseModel):
    title: str
    description: Optional[str] = None
    video_url: str
    reward_amount: float

class VideoTaskCreate(VideoTaskBase):
    pass

class VideoTask(VideoTaskBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True

class UserVideoTaskBase(BaseModel):
    status: str = "pending"

class UserVideoTaskCreate(UserVideoTaskBase):
    video_task_id: int

class UserVideoTask(UserVideoTaskBase):
    id: int
    user_id: int
    video_task_id: int
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class AvailableTask(BaseModel):
    id: int
    title: str
    description: Optional[str] = None
    video_url: str
    reward_amount: float

class UserTaskCompletion(BaseModel):
    video_task_id: int

