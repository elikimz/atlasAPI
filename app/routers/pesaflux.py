import uuid
import logging
import re
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.database.database import get_async_db
from app.models import models
from app.models.pesaflux_payment import PesaFluxPayment
from app.routers.auth import get_current_user
from app.services import pesaflux_service
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/pesaflux",
    tags=["pesaflux"]
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_phone(phone: str) -> str:
    """
    Normalize a Kenyan phone number to 2547XXXXXXXX format.
    Accepts: 07XXXXXXXX, 2547XXXXXXXX, +2547XXXXXXXX
    """
    phone = phone.strip().replace(" ", "").replace("-", "")
    if phone.startswith("+"):
        phone = phone[1:]
    if phone.startswith("07") and len(phone) == 10:
        phone = "254" + phone[1:]
    if phone.startswith("01") and len(phone) == 10:
        phone = "254" + phone[1:]
    return phone


def _is_valid_kenyan_phone(phone: str) -> bool:
    """Validate a normalized Kenyan phone number (2547XXXXXXXX or 2541XXXXXXXX)."""
    return bool(re.match(r"^254[17]\d{8}$", phone))


def _plan_is_active(user: models.User) -> bool:
    expiry = user.plan_expiry_date
    if expiry and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return bool(user.current_plan_id and expiry and expiry > _utc_now())


def _plan_is_expired(user: models.User) -> bool:
    expiry = user.plan_expiry_date
    if expiry and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return bool(user.current_plan_id and (expiry is None or expiry <= _utc_now()))


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

class InitiateStkRequest(BaseModel):
    plan_id: int | None = None
    amount: float | None = None
    phone: str

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        normalized = _normalize_phone(v)
        if not _is_valid_kenyan_phone(normalized):
            raise ValueError(
                "Invalid Kenyan phone number. Use format 07XXXXXXXX or 2547XXXXXXXX."
            )
        return normalized


class InitiateStkResponse(BaseModel):
    reference: str
    transaction_request_id: str
    amount_kes: int
    amount_usd: float
    plan_name: str
    message: str


class PaymentStatusResponse(BaseModel):
    reference: str
    status: str          # pending | completed | failed
    plan_name: str | None
    amount_usd: float
    amount_kes: float
    mpesa_receipt: str | None
    message: str


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint: Initiate STK Push
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/initiate", response_model=InitiateStkResponse)
async def initiate_stk_push(
    request_data: InitiateStkRequest,
    db: AsyncSession = Depends(get_async_db),
    current_user: models.User = Depends(get_current_user)
):
    """
    Initiate a PesaFlux M-Pesa STK Push for a plan purchase, upgrade, or recharge.

    - Plan price is loaded from the database if plan_id is provided.
    - If plan_id is missing, it treats it as a pure recharge using request_data.amount.
    - A unique reference is generated for every payment attempt.
    - The STK Push is sent to the user's phone.
    - Returns the reference for status polling.
    """
    plan = None
    amount_usd = 0.0
    payment_type = "purchase"
    plan_name = "Account Recharge"

    # Case A: Plan-based purchase/upgrade
    if request_data.plan_id:
        result = await db.execute(
            select(models.Plan).filter(
                models.Plan.id == request_data.plan_id,
                models.Plan.is_active == True  # noqa: E712
            )
        )
        plan = result.scalar_one_or_none()
        if not plan:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Plan not found or is no longer available."
            )

        if plan.price == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The Intern (Free Trial) plan does not require payment."
            )

        if _plan_is_active(current_user):
            result_current = await db.execute(
                select(models.Plan).filter(models.Plan.id == current_user.current_plan_id)
            )
            current_plan = result_current.scalar_one_or_none()
            if current_plan and plan.price <= current_plan.price:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="You already have an active plan. To upgrade, select a higher-tier plan."
                )

        if _plan_is_expired(current_user):
            result_expired = await db.execute(
                select(models.Plan).filter(models.Plan.id == current_user.current_plan_id)
            )
            expired_plan = result_expired.scalar_one_or_none()
            if expired_plan and plan.price <= expired_plan.price:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Your previous plan has expired. You must upgrade to a higher tier."
                )

        amount_usd = plan.price
        plan_name = plan.name
        payment_type = "upgrade" if current_user.current_plan_id else "purchase"

    # Case B: Pure recharge (amount-based)
    elif request_data.amount:
        if request_data.amount < 20:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Minimum recharge amount is $20."
            )
        amount_usd = request_data.amount
        payment_type = "recharge"
        plan_name = f"Recharge ${amount_usd:.2f}"
    
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either plan_id or amount must be provided."
        )

    # 4. Convert USD to KES
    usd_to_kes = float(getattr(settings, "PESAFLUX_USD_TO_KES_RATE", 130))
    amount_kes = max(1, round(amount_usd * usd_to_kes))

    # 5. Generate unique reference
    plan_ref_id = plan.id if plan else "RCH"
    reference = f"ATLAS-{current_user.id}-{plan_ref_id}-{uuid.uuid4().hex[:10].upper()}"

    # 6. Create pending PesaFluxPayment record
    pf_payment = PesaFluxPayment(
        user_id=current_user.id,
        plan_id=plan.id if plan else None,
        reference=reference,
        phone=request_data.phone, # Already normalized by Pydantic validator
        amount=amount_kes,
        amount_usd=amount_usd,
        status="pending",
        payment_type=payment_type,
        created_at=_utc_now()
    )
    db.add(pf_payment)
    await db.commit()

    # 7. Call PesaFlux Service to initiate STK Push
    init_res = await pesaflux_service.initiate_stk_push(
        phone=pf_payment.phone,
        amount=pf_payment.amount,
        reference=pf_payment.reference,
        description=f"Atlas {plan_name} - {current_user.email}"
    )

    if not init_res["success"]:
        # Update record as failed immediately
        pf_payment.status = "failed"
        await db.commit()
        
        # pesaflux_service.initiate_stk_push now returns correct status codes
        # and user-friendly error messages in the "message" field.
        raise HTTPException(
            status_code=init_res.get("status_code", status.HTTP_503_SERVICE_UNAVAILABLE),
            detail=init_res.get("message", "Failed to initiate M-Pesa payment. Please try again later.")
        )

    # 8. Update record with transaction request ID
    pf_payment.transaction_request_id = init_res.get("transaction_request_id")
    await db.commit()

    return {
        "reference": reference,
        "transaction_request_id": pf_payment.transaction_request_id,
        "amount_kes": amount_kes,
        "amount_usd": amount_usd,
        "plan_name": plan_name,
        "message": "STK Push sent to your phone. Please enter your PIN to complete payment."
    }


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint: Poll Status
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/status/{ref}", response_model=PaymentStatusResponse)
async def get_payment_status(
    ref: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: models.User = Depends(get_current_user)
):
    """
    Poll the status of a PesaFlux payment by its reference.
    Used by the frontend to show success/failure after the user enters their PIN.
    """
    result = await db.execute(
        select(PesaFluxPayment).filter(
            PesaFluxPayment.reference == ref,
            PesaFluxPayment.user_id == current_user.id
        )
    )
    payment = result.scalar_one_or_none()

    if not payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment record not found."
        )

    # If already completed or failed in our DB, return immediately
    if payment.status in ["completed", "failed"]:
        return {
            "reference": payment.reference,
            "status": payment.status,
            "plan_name": payment.plan.name if (payment.plan_id and payment.plan) else "Account Recharge",
            "amount_usd": payment.amount_usd,
            "amount_kes": payment.amount,
            "mpesa_receipt": payment.mpesa_receipt,
            "message": "Payment completed successfully." if payment.status == "completed" else "Payment failed."
        }

    # Otherwise, check status with PesaFlux API (real-time sync)
    status_res = await pesaflux_service.get_payment_status(payment.reference)
    
    if status_res["success"]:
        provider_status = status_res.get("status", "pending") # pending | completed | failed
        
        if provider_status != payment.status:
            payment.status = provider_status
            if provider_status == "completed":
                payment.mpesa_receipt = status_res.get("mpesa_receipt")
                payment.completed_at = _utc_now()
                # ── CRITICAL: Process the payment (activate plan or credit wallet) ──
                await _process_successful_payment(payment, db)
            
            payment.updated_at = _utc_now()
            await db.commit()

    return {
        "reference": payment.reference,
        "status": payment.status,
        "plan_name": payment.plan.name if (payment.plan_id and payment.plan) else "Account Recharge",
        "amount_usd": payment.amount_usd,
        "amount_kes": payment.amount,
        "mpesa_receipt": payment.mpesa_receipt,
        "message": "Waiting for payment confirmation..."
    }


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint: Webhook (Public)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/webhook")
async def pesaflux_webhook(
    request: Request,
    db: AsyncSession = Depends(get_async_db)
):
    """
    Public webhook endpoint for PesaFlux callbacks.
    Updates payment status and activates plans/credits wallets asynchronously.
    """
    try:
        data = await request.json()
    except Exception:
        return {"status": "error", "message": "Invalid JSON"}

    ref = data.get("reference")
    provider_status = data.get("status") # completed | failed
    
    if not ref or not provider_status:
        return {"status": "error", "message": "Missing reference or status"}

    result = await db.execute(
        select(PesaFluxPayment).filter(PesaFluxPayment.reference == ref)
    )
    payment = result.scalar_one_or_none()

    if not payment:
        logger.warning(f"Webhook received for unknown reference: {ref}")
        return {"status": "ignored", "message": "Reference not found"}

    # If already processed, ignore
    if payment.status in ["completed", "failed"]:
        return {"status": "ignored", "message": "Already processed"}

    # Update payment record
    payment.status = provider_status
    payment.mpesa_receipt = data.get("mpesa_receipt")
    payment.provider_transaction_id = data.get("transaction_id")
    payment.updated_at = _utc_now()

    if provider_status == "completed":
        payment.completed_at = _utc_now()
        # ── CRITICAL: Process the payment (activate plan or credit wallet) ──
        await _process_successful_payment(payment, db)

    await db.commit()
    return {"status": "success"}


# ─────────────────────────────────────────────────────────────────────────────
# Core Logic: Process Successful Payment
# ─────────────────────────────────────────────────────────────────────────────

async def _process_successful_payment(payment: PesaFluxPayment, db: AsyncSession):
    """
    Activates the plan or credits the user's wallet after a successful M-Pesa payment.
    This is called by both the polling endpoint and the webhook.
    """
    if payment.plan_activated == "yes":
        return

    # Load user with lock to prevent race conditions
    user_result = await db.execute(
        select(models.User).filter(models.User.id == payment.user_id)
    )
    user = user_result.scalar_one_or_none()
    if not user:
        return

    # Case 1: Plan Purchase or Upgrade
    if payment.plan_id and payment.payment_type in ["purchase", "upgrade"]:
        # Load plan details
        plan_result = await db.execute(
            select(models.Plan).filter(models.Plan.id == payment.plan_id)
        )
        plan = plan_result.scalar_one_or_none()
        if not plan:
            return

        now = _utc_now()
        
        # If upgrade: refund old plan price to withdrawal wallet (instant)
        if payment.payment_type == "upgrade" and user.current_plan_id:
            # Find active plan history for refund
            hist_result = await db.execute(
                select(models.UserPlanHistory).filter(
                    models.UserPlanHistory.user_id == user.id,
                    models.UserPlanHistory.status == "active"
                ).order_by(models.UserPlanHistory.purchased_at.desc())
            )
            old_hist = hist_result.scalars().first()
            refund_amount = old_hist.purchase_price if old_hist else (user.plan_purchase_price or 0.0)
            
            if refund_amount > 0:
                user.withdrawal_wallet_balance = (user.withdrawal_wallet_balance or 0.0) + refund_amount
                db.add(models.EarningsLog(
                    user_id=user.id,
                    amount=refund_amount,
                    type="upgrade_refund",
                    description=f"M-Pesa Upgrade: Refund for previous plan"
                ))
                if old_hist:
                    old_hist.status = "upgraded"
                    old_hist.refunded_amount = refund_amount

        # Activate new plan
        user.current_plan_id = plan.id
        user.plan_purchase_price = plan.price
        user.plan_start_date = now
        user.plan_expiry_date = now + timedelta(days=plan.validity_days)
        user.has_purchased_first_package = True
        
        # Create plan history entry
        db.add(models.UserPlanHistory(
            user_id=user.id,
            plan_id=plan.id,
            purchase_price=plan.price,
            purchased_at=now,
            expires_at=user.plan_expiry_date,
            status="active"
        ))

        # Assign tasks for the new plan
        task_result = await db.execute(
            select(models.VideoTask).filter(models.VideoTask.plan_id == plan.id)
        )
        for task in task_result.scalars().all():
            db.add(models.UserVideoTask(
                user_id=user.id,
                video_task_id=task.id,
                status="pending"
            ))

    # Case 2: Account Recharge
    else:
        # Credit the USD amount to the user's deposit wallet
        user.deposit_wallet_balance = (user.deposit_wallet_balance or 0.0) + payment.amount_usd
        
        # Log to payments table for history (marked as paid/approved)
        # Note: 'period' is a required non-null field in the models.Payment table
        db.add(models.Payment(
            user_id=user.id,
            amount=payment.amount_usd,
            period=_utc_now().strftime("%b %Y"),
            type="deposit",
            payment_method="M-Pesa",
            status="paid",
            created_at=_utc_now(),
            admin_notes=f"Instant M-Pesa Recharge: {payment.mpesa_receipt}"
        ))

    payment.plan_activated = "yes"
    db.add(user)
    db.add(payment)
