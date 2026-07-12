"""
PesaFlux M-Pesa STK Push payment model.

This is a NEW, ISOLATED model file.
It does NOT modify any existing models.
The PesaFluxPayment table is separate from the existing `payments` table.
"""

from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from app.database.database import Base


class PesaFluxPayment(Base):
    """
    Tracks every PesaFlux M-Pesa STK Push payment attempt.

    Lifecycle:
        pending   -> STK Push initiated, waiting for user to complete on phone
        completed -> Webhook confirmed success; deposit wallet credited & plan activated
        failed    -> Webhook confirmed failure (cancelled, timeout, insufficient funds, etc.)
    """
    __tablename__ = "pesaflux_payments"

    id = Column(Integer, primary_key=True, index=True)

    # User who initiated the payment
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Plan the user is purchasing/upgrading to
    plan_id = Column(Integer, ForeignKey("plans.id", ondelete="SET NULL"), nullable=True, index=True)

    # Our unique reference sent to PesaFlux during STK initiation
    reference = Column(String, unique=True, nullable=False, index=True)

    # PesaFlux transaction_request_id returned after STK initiation
    transaction_request_id = Column(String, nullable=True, index=True)

    # PesaFlux TransactionID from webhook/status response
    provider_transaction_id = Column(String, nullable=True, index=True)

    # M-Pesa receipt number (e.g. SIS88JC7AM) from webhook
    mpesa_receipt = Column(String, nullable=True)

    # Phone number used for the STK Push (in 2547XXXXXXXX format)
    phone = Column(String, nullable=False)

    # Amount in KES (converted from USD at time of initiation)
    amount = Column(Float, nullable=False)

    # Amount in USD (the plan price at time of initiation)
    amount_usd = Column(Float, nullable=False)

    # Payment status: pending | completed | failed
    status = Column(String, default="pending", nullable=False, index=True)

    # Always "pesaflux" for traceability
    provider = Column(String, default="pesaflux", nullable=False)

    # Whether the plan was already activated (prevents double-activation)
    plan_activated = Column(String, default="no", nullable=False)  # no | yes

    # Whether this was a first purchase or upgrade
    payment_type = Column(String, default="purchase", nullable=False)  # purchase | upgrade

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    user = relationship("app.models.models.User", back_populates="pesaflux_payments")
    plan = relationship("app.models.models.Plan")
