from sqlalchemy import Column, Integer, String, DateTime, Float, ForeignKey, Boolean, func
from sqlalchemy.orm import relationship
from app.database.database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    email = Column(String, unique=True, index=True, nullable=False)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    deposit_wallet_balance = Column(Float, default=0.0)
    withdrawal_wallet_balance = Column(Float, default=0.0)
    
    # Referral Tracking
    referred_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    referral_code_used = Column(String, nullable=True)

    # Relationships
    certifications = relationship("UserCertification", back_populates="user")
    referral_codes = relationship("ReferralCode", back_populates="user")
    payments = relationship("Payment", back_populates="user")
    evaluations = relationship("Evaluation", back_populates="user")
    video_tasks = relationship("UserVideoTask", back_populates="user")
    
    # Self-referential relationship for multi-tier
    referrer = relationship("User", remote_side=[id], backref="referrals")

class OTP(Base):
    __tablename__ = "otps"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, index=True, nullable=False)
    otp_code = Column(String, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Certification(Base):
    __tablename__ = "certifications"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    estimated_time = Column(String, nullable=True)
    steps_count = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)

class UserCertification(Base):
    __tablename__ = "user_certifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    certification_id = Column(Integer, ForeignKey("certifications.id"))
    status = Column(String, default="available") # available, in_progress, completed
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="certifications")

class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    required_certification_id = Column(Integer, ForeignKey("certifications.id"), nullable=True)
    status = Column(String, default="locked") # locked, available, active

class ReferralCode(Base):
    __tablename__ = "referral_codes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    code = Column(String, unique=True, index=True, nullable=False)
    signups_count = Column(Integer, default=0)
    trained_count = Column(Integer, default=0)
    earned_amount = Column(Float, default=0.0)
    task_rebate_amount = Column(Float, default=0.0) # New: specifically for task-based rebates

    user = relationship("User", back_populates="referral_codes")

class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    amount = Column(Float, nullable=False)
    period = Column(String, nullable=False)
    status = Column(String, default="pending") # pending, paid, in_progress
    payout_date = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="payments")

class Evaluation(Base):
    __tablename__ = "evaluations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    name = Column(String, nullable=False)
    episodes_completed = Column(Integer, default=0)
    total_episodes_required = Column(Integer, default=5)
    episodes_passing_audit = Column(Integer, default=0)
    status = Column(String, default="in_progress")

    user = relationship("User", back_populates="evaluations")

class VideoTask(Base):
    __tablename__ = "video_tasks"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    description = Column(String, nullable=True)
    video_url = Column(String, nullable=False)
    reward_amount = Column(Float, default=0.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class UserVideoTask(Base):
    __tablename__ = "user_video_tasks"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    video_task_id = Column(Integer, ForeignKey("video_tasks.id"))
    status = Column(String, default="pending") # pending, completed, rejected
    completed_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="video_tasks")
    video_task = relationship("VideoTask")
