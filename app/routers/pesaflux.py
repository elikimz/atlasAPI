"""
PesaFlux M-Pesa STK Push router.

This is a NEW, ISOLATED router.
It does NOT modify any existing routes or business logic.

Endpoints:
    POST /pesaflux/initiate        - Authenticated. Initiates STK Push for a plan.
    GET  /pesaflux/status/{ref}    - Authenticated. Polls payment status by reference.
    POST /pesaflux/webhook         - Public. Receives PesaFlux payment callbacks.

Security:
    - PESAFLUX_API_KEY is NEVER exposed to the frontend or returned in any response.
    - All user-facing endpoints require JWT authentication.
    - Webhook is idempotent: duplicate callbacks are safely ignored.
    - Plan price is always loaded from the database — never trusted from the frontend.
"""

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
    plan_id: int
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
    Initiate a PesaFlux M-Pesa STK Push for a plan purchase or upgrade.

    - Plan price is loaded from the database. Frontend cannot manipulate the amount.
    - A unique reference is generated for every payment attempt.
    - The STK Push is sent to the user's phone.
    - Returns the reference for status polling.
    """
    # 1. Load plan from DB — never trust amount from frontend
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

    # 2. Validate plan eligibility (same rules as plans.py)
    if plan.price == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The Intern (Free Trial) plan does not require payment."
        )

    if _plan_is_active(current_user):
        # Upgrade scenario: new plan must be higher tier
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

    # 3. Determine payment type
    payment_type = "upgrade" if current_user.current_plan_id else "purchase"

    # 4. Convert USD to KES
    usd_to_kes = float(getattr(settings, "PESAFLUX_USD_TO_KES_RATE", 130))
    amount_kes = max(1, round(plan.price * usd_to_kes))

    # 5. Generate unique reference
    reference = f"ATLAS-{current_user.id}-{plan.id}-{uuid.uuid4().hex[:10].upper()}"

    # 6. Create pending PesaFluxPayment record
    pf_payment = PesaFluxPayment(
        user_id=current_user.id,
        plan_id=plan.id,
        reference=reference,
        transaction_request_id=None,
        phone=request_data.phone,
        amount=float(amount_kes),
        amount_usd=plan.price,
        status="pending",
        provider="pesaflux",
        plan_activated="no",
        payment_type=payment_type
    )
    db.add(pf_payment)
    await db.commit()
    await db.refresh(pf_payment)

    # 7. Initiate STK Push via PesaFlux API
    stk_result = await pesaflux_service.initiate_stk_push(
        phone=request_data.phone,
        amount_kes=amount_kes,
        reference=reference
    )

    if not stk_result["success"]:
        # Mark payment as failed
        pf_payment.status = "failed"
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=stk_result["error"] or "Failed to initiate M-Pesa payment. Please try again."
        )

    # 8. Store transaction_request_id
    pf_payment.transaction_request_id = stk_result["transaction_request_id"]
    await db.commit()

    logger.info(
        "PesaFlux STK Push initiated: user_id=%s plan_id=%s reference=%s txn_id=%s",
        current_user.id, plan.id, reference, stk_result["transaction_request_id"]
    )

    return InitiateStkResponse(
        reference=reference,
        transaction_request_id=stk_result["transaction_request_id"],
        amount_kes=amount_kes,
        amount_usd=plan.price,
        plan_name=plan.name,
        message=(
            f"M-Pesa STK Push sent to {request_data.phone}. "
            "Please check your phone and enter your M-Pesa PIN to complete the payment."
        )
    )


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint: Check Payment Status (polling)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/status/{reference}", response_model=PaymentStatusResponse)
async def get_payment_status(
    reference: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: models.User = Depends(get_current_user)
):
    """
    Check the status of a PesaFlux payment by reference.

    - Only the owner of the payment can check its status.
    - If the payment is still pending, also queries PesaFlux API for live status.
    """
    # 1. Load payment record
    result = await db.execute(
        select(PesaFluxPayment).filter(
            PesaFluxPayment.reference == reference,
            PesaFluxPayment.user_id == current_user.id
        )
    )
    pf_payment = result.scalar_one_or_none()
    if not pf_payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Payment not found."
        )

    # 2. Load plan name
    plan_name = None
    if pf_payment.plan_id:
        result_plan = await db.execute(
            select(models.Plan).filter(models.Plan.id == pf_payment.plan_id)
        )
        plan = result_plan.scalar_one_or_none()
        plan_name = plan.name if plan else None

    # 3. If already completed or failed, return stored status
    if pf_payment.status in ("completed", "failed"):
        return PaymentStatusResponse(
            reference=pf_payment.reference,
            status=pf_payment.status,
            plan_name=plan_name,
            amount_usd=pf_payment.amount_usd,
            amount_kes=pf_payment.amount,
            mpesa_receipt=pf_payment.mpesa_receipt,
            message=_status_message(pf_payment.status, plan_name)
        )

    # 4. Payment is still pending — query PesaFlux for live status
    if not pf_payment.transaction_request_id:
        return PaymentStatusResponse(
            reference=pf_payment.reference,
            status="pending",
            plan_name=plan_name,
            amount_usd=pf_payment.amount_usd,
            amount_kes=pf_payment.amount,
            mpesa_receipt=None,
            message="Payment is being processed. Please wait..."
        )

    status_result = await pesaflux_service.check_transaction_status(
        pf_payment.transaction_request_id
    )

    if status_result["success"]:
        tx_status = status_result.get("status", "Unknown")

        if tx_status == "Completed":
            # Payment completed via polling — process it
            await _process_successful_payment(db, pf_payment, status_result)
            return PaymentStatusResponse(
                reference=pf_payment.reference,
                status="completed",
                plan_name=plan_name,
                amount_usd=pf_payment.amount_usd,
                amount_kes=pf_payment.amount,
                mpesa_receipt=pf_payment.mpesa_receipt,
                message=_status_message("completed", plan_name)
            )
        elif tx_status in ("Failed", "Cancelled"):
            pf_payment.status = "failed"
            pf_payment.updated_at = _utc_now()
            await db.commit()
            return PaymentStatusResponse(
                reference=pf_payment.reference,
                status="failed",
                plan_name=plan_name,
                amount_usd=pf_payment.amount_usd,
                amount_kes=pf_payment.amount,
                mpesa_receipt=None,
                message="Payment was not completed. You can try again."
            )

    # Still pending
    return PaymentStatusResponse(
        reference=pf_payment.reference,
        status="pending",
        plan_name=plan_name,
        amount_usd=pf_payment.amount_usd,
        amount_kes=pf_payment.amount,
        mpesa_receipt=None,
        message="Waiting for M-Pesa confirmation. Please complete the prompt on your phone."
    )


def _status_message(payment_status: str, plan_name: str | None) -> str:
    if payment_status == "completed":
        return f"Payment successful! Your {plan_name or 'plan'} has been activated."
    if payment_status == "failed":
        return "Payment failed or was cancelled. Please try again."
    return "Payment is pending. Please complete the M-Pesa prompt on your phone."


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint: PesaFlux Webhook
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/webhook")
async def pesaflux_webhook(
    request: Request,
    db: AsyncSession = Depends(get_async_db)
):
    """
    Receive PesaFlux payment callback (webhook).

    This endpoint is PUBLIC — PesaFlux calls it directly.
    It is idempotent: duplicate callbacks for the same transaction are safely ignored.

    Expected payload:
    {
        "ResponseCode": 0,
        "ResponseDescription": "...",
        "MerchantRequestID": "...",
        "CheckoutRequestID": "...",
        "TransactionID": "...",
        "TransactionAmount": 100,
        "TransactionReceipt": "SIS88JC7AM",
        "TransactionDate": "20240928222012",
        "TransactionReference": "ATLAS-...",
        "Msisdn": "254769290734"
    }
    """
    try:
        payload = await request.json()
    except Exception:
        logger.warning("PesaFlux webhook: invalid JSON payload received.")
        return {"status": "error", "message": "Invalid JSON"}

    logger.info("PesaFlux webhook received: %s", {
        k: v for k, v in payload.items() if k != "api_key"
    })

    # Validate required fields
    if not payload.get("TransactionID"):
        logger.warning("PesaFlux webhook: missing TransactionID.")
        return {"status": "error", "message": "Missing TransactionID"}

    reference = payload.get("TransactionReference", "")
    response_code = payload.get("ResponseCode")
    transaction_id = payload.get("TransactionID", "")
    receipt = payload.get("TransactionReceipt", "")
    amount = payload.get("TransactionAmount")
    phone = payload.get("Msisdn", "")

    # Find the payment record by our reference
    result = await db.execute(
        select(PesaFluxPayment).filter(PesaFluxPayment.reference == reference)
    )
    pf_payment = result.scalar_one_or_none()

    if not pf_payment:
        logger.warning(
            "PesaFlux webhook: no payment found for reference=%s txn_id=%s",
            reference, transaction_id
        )
        # Return 200 to acknowledge receipt (PesaFlux may retry on non-200)
        return {"status": "received", "message": "Reference not found — acknowledged"}

    # Idempotency: if already processed, do not process again
    if pf_payment.status != "pending":
        logger.info(
            "PesaFlux webhook: payment reference=%s already in status=%s — skipping.",
            reference, pf_payment.status
        )
        return {"status": "success", "message": "Already processed"}

    # Store provider transaction ID
    pf_payment.provider_transaction_id = transaction_id
    pf_payment.updated_at = _utc_now()

    if response_code == 0:
        # ── SUCCESS ──────────────────────────────────────────────────────────
        # Validate amount matches expected (within 1 KES tolerance for rounding)
        if amount is not None:
            try:
                received_amount = float(amount)
                expected_amount = float(pf_payment.amount)
                if abs(received_amount - expected_amount) > 1.0:
                    logger.error(
                        "PesaFlux webhook: amount mismatch for reference=%s "
                        "expected=%.2f received=%.2f",
                        reference, expected_amount, received_amount
                    )
                    pf_payment.status = "failed"
                    await db.commit()
                    return {"status": "error", "message": "Amount mismatch"}
            except (TypeError, ValueError):
                pass  # If amount parsing fails, proceed with activation

        pf_payment.mpesa_receipt = receipt
        await _process_successful_payment(db, pf_payment, {
            "receipt": receipt,
            "amount": str(amount),
            "phone": phone
        })

        logger.info(
            "PesaFlux webhook: payment completed reference=%s receipt=%s",
            reference, receipt
        )
        return {"status": "success", "message": "Payment processed successfully"}

    else:
        # ── FAILURE ──────────────────────────────────────────────────────────
        pf_payment.status = "failed"
        await db.commit()
        logger.info(
            "PesaFlux webhook: payment failed reference=%s code=%s desc=%s",
            reference, response_code, payload.get("ResponseDescription")
        )
        return {"status": "received", "message": "Failed transaction logged"}


# ─────────────────────────────────────────────────────────────────────────────
# Internal: Process Successful Payment
# ─────────────────────────────────────────────────────────────────────────────

async def _process_successful_payment(
    db: AsyncSession,
    pf_payment: PesaFluxPayment,
    status_data: dict
) -> None:
    """
    Process a confirmed successful PesaFlux payment.

    This function:
    1. Marks the PesaFluxPayment as completed.
    2. Credits the user's deposit_wallet_balance with the USD amount.
    3. Activates the plan (purchase or upgrade) using the SAME logic as plans.py.
    4. Is idempotent: checks plan_activated flag before activating.

    IMPORTANT: This replicates the plan activation logic from plans.py WITHOUT
    modifying plans.py. This is intentional to maintain isolation.
    """
    # Guard: already activated
    if pf_payment.plan_activated == "yes":
        return

    now = _utc_now()

    # Mark payment completed
    pf_payment.status = "completed"
    pf_payment.completed_at = now
    pf_payment.updated_at = now
    if status_data.get("receipt"):
        pf_payment.mpesa_receipt = status_data["receipt"]

    # Load user
    result_user = await db.execute(
        select(models.User).filter(models.User.id == pf_payment.user_id)
    )
    user = result_user.scalar_one_or_none()
    if not user:
        logger.error(
            "PesaFlux: user_id=%s not found during payment processing for reference=%s",
            pf_payment.user_id, pf_payment.reference
        )
        await db.commit()
        return

    # Load plan
    result_plan = await db.execute(
        select(models.Plan).filter(models.Plan.id == pf_payment.plan_id)
    )
    plan = result_plan.scalar_one_or_none()
    if not plan:
        logger.error(
            "PesaFlux: plan_id=%s not found during payment processing for reference=%s",
            pf_payment.plan_id, pf_payment.reference
        )
        await db.commit()
        return

    # ── Credit deposit wallet with USD amount ──────────────────────────────
    # The user paid KES; we credit the USD equivalent (plan price) to their deposit wallet.
    # This makes the deposit wallet consistent with existing manual deposit flow.
    user.deposit_wallet_balance = (user.deposit_wallet_balance or 0.0) + pf_payment.amount_usd

    # ── Activate plan ──────────────────────────────────────────────────────
    is_upgrade = pf_payment.payment_type == "upgrade"

    if is_upgrade:
        # Upgrade: find current active plan history, mark as upgraded, refund old price
        result_old_history = await db.execute(
            select(models.UserPlanHistory).filter(
                models.UserPlanHistory.user_id == user.id,
                models.UserPlanHistory.plan_id == user.current_plan_id,
                models.UserPlanHistory.status == "active"
            ).order_by(models.UserPlanHistory.purchased_at.desc())
        )
        old_history = result_old_history.scalars().first()
        refund_amount = (
            old_history.purchase_price if old_history
            else (user.plan_purchase_price or 0.0)
        )

        if old_history:
            old_history.status = "upgraded"
            old_history.refunded_amount = refund_amount
            db.add(old_history)

        # Immediate refund to withdrawal wallet (same as plans.py upgrade logic)
        if refund_amount > 0:
            user.withdrawal_wallet_balance = (user.withdrawal_wallet_balance or 0.0) + refund_amount
            db.add(models.EarningsLog(
                user_id=user.id,
                amount=refund_amount,
                type="upgrade_refund",
                description="Immediate upgrade refund for previous plan (M-Pesa payment)"
            ))
            upgrade_refund = models.UpgradeRefund(
                user_id=user.id,
                amount=refund_amount,
                status="released",
                release_at=now,
                released_at=now,
                plan_history_id=old_history.id if old_history else None
            )
            db.add(upgrade_refund)

        # Clean up old plan's pending tasks
        await db.execute(
            models.UserVideoTask.__table__.delete().where(
                models.UserVideoTask.user_id == user.id,
                models.UserVideoTask.status == "pending"
            )
        )

        if plan.price > 0:
            user.has_purchased_first_package = True

    else:
        # Purchase: apply invite commissions (first-time purchase only)
        if not user.has_purchased_first_package and plan.price > 0:
            user.has_purchased_first_package = True

            commission_config = [
                ("tier_a_invite_earnings", 0.10),
                ("tier_b_invite_earnings", 0.04),
                ("tier_c_invite_earnings", 0.01),
            ]

            rel_result = await db.execute(
                select(models.ReferralRelationship).filter(
                    models.ReferralRelationship.user_id == user.id
                )
            )
            rel = rel_result.scalar_one_or_none()
            current_upline_id = rel.referrer_id if rel else None

            for field_name, percentage in commission_config:
                if not current_upline_id:
                    break
                upline_result = await db.execute(
                    select(models.User).filter(models.User.id == current_upline_id)
                )
                upline = upline_result.scalar_one_or_none()
                if upline:
                    commission_amount = plan.price * percentage
                    upline.withdrawal_wallet_balance = (
                        upline.withdrawal_wallet_balance or 0.0
                    ) + commission_amount
                    db.add(models.EarningsLog(
                        user_id=upline.id,
                        amount=commission_amount,
                        type="invite_commission",
                        description=(
                            f"Invite commission from {user.email} "
                            f"(Tier {field_name.split('_')[1].upper()}) via M-Pesa"
                        )
                    ))
                    code_result = await db.execute(
                        select(models.ReferralCode)
                        .filter(models.ReferralCode.user_id == upline.id)
                        .limit(1)
                    )
                    ref_code = code_result.scalar_one_or_none()
                    if ref_code:
                        current_val = getattr(ref_code, field_name, 0.0) or 0.0
                        setattr(ref_code, field_name, current_val + commission_amount)
                        ref_code.earned_amount = (ref_code.earned_amount or 0.0) + commission_amount
                    next_rel_result = await db.execute(
                        select(models.ReferralRelationship).filter(
                            models.ReferralRelationship.user_id == upline.id
                        )
                    )
                    next_rel = next_rel_result.scalar_one_or_none()
                    current_upline_id = next_rel.referrer_id if next_rel else None
                else:
                    break

    # ── Set user plan fields ───────────────────────────────────────────────
    # Deduct from deposit wallet (we just credited it above, so net = plan activation)
    user.deposit_wallet_balance -= plan.price
    user.current_plan_id = plan.id
    user.plan_purchase_price = plan.price
    user.plan_start_date = now
    user.plan_expiry_date = now + timedelta(days=plan.validity_days)

    # ── Create plan history record ─────────────────────────────────────────
    new_plan_history = models.UserPlanHistory(
        user_id=user.id,
        plan_id=plan.id,
        purchase_price=plan.price,
        purchased_at=now,
        expires_at=user.plan_expiry_date,
        status="active",
        refunded_amount=0.0
    )
    db.add(new_plan_history)
    db.add(user)

    # ── Auto-assign video tasks for the new plan ───────────────────────────
    result_tasks = await db.execute(
        select(models.VideoTask).filter(models.VideoTask.plan_id == plan.id)
    )
    plan_tasks = result_tasks.scalars().all()
    for task in plan_tasks:
        existing_task_result = await db.execute(
            select(models.UserVideoTask).filter(
                models.UserVideoTask.user_id == user.id,
                models.UserVideoTask.video_task_id == task.id
            )
        )
        if not existing_task_result.scalar_one_or_none():
            db.add(models.UserVideoTask(
                user_id=user.id,
                video_task_id=task.id,
                status="pending"
            ))

    # ── Mark plan as activated (idempotency guard) ─────────────────────────
    pf_payment.plan_activated = "yes"

    await db.commit()

    logger.info(
        "PesaFlux: plan activated for user_id=%s plan=%s reference=%s",
        user.id, plan.name, pf_payment.reference
    )
