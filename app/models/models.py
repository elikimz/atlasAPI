from sqlalchemy import Column, Integer, String, DateTime, Float, ForeignKey, Boolean, func
from sqlalchemy.orm import relationship
from app.database.database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    email = Column(String, unique=True, index=True, nullable=False)
    role = Column(String, default="user") # user, admin
    is_admin = Column(Boolean, default=False) # Keep for backward compatibility
    is_trained = Column(Boolean, default=False)
    deposit_wallet_balance = Column(Float, default=0.0)
    withdrawal_wallet_balance = Column(Float, default=0.0)
    performance_bonus_balance = Column(Float, default=0.0)
    referral_code = Column(String, unique=True, index=True, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # New plan-related fields
    current_plan_id = Column(Integer, ForeignKey("plans.id"), nullable=True)
    plan_start_date = Column(DateTime(timezone=True), nullable=True)
    plan_expiry_date = Column(DateTime(timezone=True), nullable=True)
    plan_purchase_price = Column(Float, default=0.0)
    has_purchased_first_package = Column(Boolean, default=False)

    # Security
    withdrawal_password = Column(String, nullable=True) # Hashed withdrawal PIN/password
    is_suspended = Column(Boolean, default=False)

    # Relationships
    certifications = relationship("UserCertification", back_populates="user", cascade="all, delete-orphan", passive_deletes=True)
    referral_codes = relationship("ReferralCode", back_populates="user", cascade="all, delete-orphan", passive_deletes=True)
    payments = relationship("Payment", back_populates="user", cascade="all, delete-orphan", passive_deletes=True)
    evaluations = relationship("Evaluation", back_populates="user", cascade="all, delete-orphan", passive_deletes=True)
    video_tasks = relationship("UserVideoTask", back_populates="user", cascade="all, delete-orphan", passive_deletes=True)
    current_plan = relationship("Plan", foreign_keys=[current_plan_id])
    withdrawal_accounts = relationship("WithdrawalAccount", back_populates="user", cascade="all, delete-orphan", passive_deletes=True)
    plan_history = relationship("UserPlanHistory", back_populates="user", cascade="all, delete-orphan", passive_deletes=True)

class OTP(Base):
    __tablename__ = "otps"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, index=True, nullable=False)
    otp_code = Column(String, nullable=False)
    is_used = Column(Boolean, default=False) # New: track usage
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    ip_address = Column(String, nullable=True) # New: for security audit

class Certification(Base):
    __tablename__ = "certifications"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    estimated_time = Column(String, nullable=True)
    video_url = Column(String, nullable=True)
    steps_count = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)

class UserCertification(Base):
    __tablename__ = "user_certifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    certification_id = Column(Integer, ForeignKey("certifications.id", ondelete="CASCADE"))
    status = Column(String, default="available") # available, in_progress, completed
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="certifications")

class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    required_certification_id = Column(Integer, ForeignKey("certifications.id", ondelete="SET NULL"), nullable=True)
    status = Column(String, default="locked") # locked, available, active

class ReferralCode(Base):
    __tablename__ = "referral_codes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    code = Column(String, unique=True, index=True, nullable=False)
    signups_count = Column(Integer, default=0)
    trained_count = Column(Integer, default=0)
    # Tier-specific invite commissions
    tier_a_invite_earnings = Column(Float, default=0.0)
    tier_b_invite_earnings = Column(Float, default=0.0)
    tier_c_invite_earnings = Column(Float, default=0.0)
    
    # Tier-specific task rebates
    tier_a_task_rebate = Column(Float, default=0.0)
    tier_b_task_rebate = Column(Float, default=0.0)
    tier_c_task_rebate = Column(Float, default=0.0)

    # Legacy fields (keep for backward compatibility or sum)
    earned_amount = Column(Float, default=0.0)
    task_rebate_amount = Column(Float, default=0.0)

    user = relationship("User", back_populates="referral_codes")

class ReferralRelationship(Base):
    __tablename__ = "referral_relationships"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    referrer_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    referral_code_used = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", foreign_keys=[user_id])
    referrer = relationship("User", foreign_keys=[referrer_id])

class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    amount = Column(Float, nullable=False)
    period = Column(String, nullable=False)
    status = Column(String, default="pending") # pending, paid, in_progress, rejected, cancelled
    type = Column(String, default="payout") # payout, deposit
    payment_method = Column(String, nullable=True)
    network = Column(String, nullable=True)
    proof_url = Column(String, nullable=True)
    admin_notes = Column(String, nullable=True)
    payout_date = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="payments")

class Evaluation(Base):
    __tablename__ = "evaluations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    name = Column(String, nullable=False)
    episodes_completed = Column(Integer, default=0)
    total_episodes_required = Column(Integer, default=5)
    episodes_passing_audit = Column(Integer, default=0)
    status = Column(String, default="in_progress")

    user = relationship("User", back_populates="evaluations")

class VideoTask(Base):
    __tablename__ = "video_tasks"

    id = Column(Integer, primary_key=True, index=True)
    plan_id = Column(Integer, ForeignKey("plans.id", ondelete="CASCADE"), nullable=True)
    title = Column(String, nullable=False)
    description = Column(String, nullable=True)
    video_url = Column(String, nullable=False)
    reward_amount = Column(Float, default=0.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    plan = relationship("Plan")

class UserVideoTask(Base):
    __tablename__ = "user_video_tasks"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    video_task_id = Column(Integer, ForeignKey("video_tasks.id", ondelete="CASCADE"))
    status = Column(String, default="pending") # pending, completed, rejected
    completed_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="video_tasks")
    video_task = relationship("VideoTask")

class Plan(Base):
    __tablename__ = "plans"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    price = Column(Float, nullable=False)
    daily_tasks_limit = Column(Integer, default=5)
    validity_days = Column(Integer, default=30)
    description = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    is_upgrade_only = Column(Boolean, default=False) # New field

class UserPlanHistory(Base):
    __tablename__ = "user_plan_history"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    plan_id = Column(Integer, ForeignKey("plans.id", ondelete="CASCADE")) # Updated foreign key
    purchase_price = Column(Float, nullable=False)
    purchased_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    status = Column(String, default="active") # active, expired, upgraded (New status)
    refunded_amount = Column(Float, default=0.0) # New field

    user = relationship("User", back_populates="plan_history")
    plan = relationship("Plan") # Updated relationship

class WithdrawalAccount(Base):
    __tablename__ = "withdrawal_accounts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    type = Column(String, nullable=False) # crypto, mpesa, wise, bank
    label = Column(String, nullable=True) # Primary, Work, etc.
    address = Column(String, nullable=False) # Wallet address or phone number
    network = Column(String, nullable=True) # ERC20, BEP20, etc.
    is_verified = Column(Boolean, default=False)
    is_primary = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="withdrawal_accounts")

class UpgradeRefund(Base):
    """
    Tracks upgrade refund amounts with a 3-day (72-hour) lock mechanism.
    When a user upgrades their plan, the refund of the previous plan price is
    logged here as 'pending'. After 72 hours it transitions to 'released',
    at which point the amount is credited to the withdrawal_wallet_balance
    and counted in Total Earnings.
    """
    __tablename__ = "upgrade_refunds"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    amount = Column(Float, nullable=False)
    status = Column(String, default="pending")  # pending, released
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    release_at = Column(DateTime(timezone=True), nullable=False)  # created_at + 72 hours
    released_at = Column(DateTime(timezone=True), nullable=True)  # actual release timestamp
    plan_history_id = Column(Integer, ForeignKey("user_plan_history.id", ondelete="SET NULL"), nullable=True)

    user = relationship("User", backref="upgrade_refunds")


class AppConfig(Base):
    __tablename__ = "app_config"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, index=True, nullable=False)
    value = Column(String, nullable=False)
    description = Column(String, nullable=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

class EarningsLog(Base):
    """
    Unified log for all profit-generating events (earnings).
    Used for strict GMT-based period calculations (Today, This Week, This Month).
    Excluded: direct recharges/deposits.
    """
    __tablename__ = "earnings_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    amount = Column(Float, nullable=False)
    type = Column(String, nullable=False)  # task_reward, task_rebate, invite_commission, upgrade_refund
    description = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", backref="earnings_logs")


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True) # Null means global notification
    title = Column(String, nullable=False)
    message = Column(String, nullable=False)
    type = Column(String, default="info") # info, success, warning, error
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", backref="notifications")
