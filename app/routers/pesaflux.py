import uuid
import logging
import re
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.database.database import get_async_db
from app.models import models
from app.models.pesaflux_payment import PesaFluxPayment
from app.routers.auth import get_current_user
from app.services import pesaflux_service
from app.services.cache import invalidate_shared_cache, invalidate_user_cache
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


def _amount_matches(reported_amount: object, expected_amount: float) -> bool:
    try:
        return abs(float(reported_amount) - float(expected_amount)) < 0.01
    except (TypeError, ValueError):
        return False


def _internal_reference_matches(reported_reference: object, expected_reference: str) -> bool:
    """Validate our custom reference when the provider echoes it.

    Some PesaFlux status responses expose a provider-side transaction reference
    instead of the custom reference sent during STK initiation. The request ID
    is already tied to our pending record, so only an echoed ADPULSEAI reference
    must match exactly; provider-generated references are accepted.
    """
    if not reported_reference:
        return True
    reported = str(reported_reference)
    if reported.startswith("ADPULSEAI-"):
        return reported == expected_reference
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

class InitiateStkRequest(BaseModel):
    plan_id: int | None = Field(default=None, gt=0)
    amount: float | None = Field(default=None, gt=0)
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
    transaction_request_id: str = ""
    amount_kes: int
    amount_usd: float
    plan_name: str
    message: str
    review_payment_id: int | None = None


class PaymentStatusResponse(BaseModel):
    reference: str
    status: str          # pending | under_review | completed | failed
    plan_name: str | None
    amount_usd: float
    amount_kes: float = 0
    mpesa_receipt: str | None = None
    message: str = ""


async def _create_manual_deposit_review(
    payment: PesaFluxPayment,
    db: AsyncSession,
    provider_message: str,
) -> models.Payment:
    """Create the admin-review deposit for a recharge attempt.

    M-Pesa provider callbacks and status polling are intentionally not trusted for
    wallet crediting. The regular payments table is the single approval workflow;
    the admin must approve it before the deposit wallet changes.
    """
    review = models.Payment(
        user_id=payment.user_id,
        amount=payment.amount_usd,
        period=_utc_now().strftime("%b %Y"),
        status="under_review",
        type="deposit",
        payment_method="M-Pesa (Manual Review)",
        network="STK Push",
        destination_number=payment.phone,
        admin_notes=(
            f"Manual M-Pesa review. Reference: {payment.reference}. "
            f"Phone: {payment.phone}. Provider result: {provider_message}"
        ),
    )
    payment.status = "under_review"
    db.add(review)
    await db.commit()
    await db.refresh(review)
    await invalidate_user_cache(payment.user_id, "payments", "dashboard")
    await invalidate_shared_cache("admin_stats")
    return review


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
    """
    try:
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
            minimum_recharge_usd = 20.0
            if request_data.amount < minimum_recharge_usd:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Minimum deposit amount is $20.00."
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
        reference = f"ADPULSEAI-{current_user.id}-{plan_ref_id}-{uuid.uuid4().hex[:10].upper()}"

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
            amount_kes=pf_payment.amount,
            reference=pf_payment.reference
        )

        if not init_res["success"]:
            error_msg = init_res.get("error") or init_res.get("message") or "The M-Pesa provider did not confirm the STK request."
            error_code = init_res.get("error_code", "provider_error")

            # A recharge attempt is always visible to the admin, even when the
            # provider reports a failure or is unavailable. The admin can verify
            # the M-Pesa account externally and approve or reject the deposit.
            if payment_type == "recharge":
                review = await _create_manual_deposit_review(pf_payment, db, error_msg)
                logger.warning(
                    "M-Pesa recharge sent to manual review after provider result for reference=%s: %s",
                    reference, error_msg,
                )
                return {
                    "reference": reference,
                    "transaction_request_id": "",
                    "amount_kes": amount_kes,
                    "amount_usd": amount_usd,
                    "plan_name": plan_name,
                    "review_payment_id": review.id,
                    "message": "Your M-Pesa deposit was submitted for manual review. An administrator will verify it before crediting your wallet.",
                }

            # Plan purchases still require a confirmed provider initiation.
            pf_payment.status = "failed"
            await db.commit()
            logger.error(
                "PesaFlux STK Push failed for reference=%s: code=%s msg=%s",
                reference, error_code, error_msg
            )
            if error_code in ("config_missing", "provider_error", "network_error", "timeout"):
                http_status = status.HTTP_503_SERVICE_UNAVAILABLE
            elif error_code in ("account_not_verified", "auth_error", "auth_or_account_error"):
                http_status = status.HTTP_403_FORBIDDEN
            elif error_code in ("user_cancelled", "insufficient_balance", "subscriber_unreachable"):
                http_status = status.HTTP_400_BAD_REQUEST
            else:
                http_status = status.HTTP_503_SERVICE_UNAVAILABLE
            raise HTTPException(status_code=http_status, detail=error_msg)

        # 8. Save the provider request ID. Recharge records are immediately
        # moved to manual review; they are never auto-credited by polling/webhook.
        pf_payment.transaction_request_id = init_res.get("transaction_request_id")
        if payment_type == "recharge":
            # The prompt has only been sent at this point. Keep the attempt
            # pending until the user confirms they entered their PIN, then the
            # explicit submit-review endpoint creates the admin deposit record.
            await db.commit()
            return {
                "reference": reference,
                "transaction_request_id": pf_payment.transaction_request_id or "",
                "amount_kes": amount_kes,
                "amount_usd": amount_usd,
                "plan_name": plan_name,
                "message": "M-Pesa prompt sent. Enter your PIN, then confirm below to submit this deposit for review.",
            }

        await db.commit()
        return {
            "reference": reference,
            "transaction_request_id": pf_payment.transaction_request_id or "",
            "amount_kes": amount_kes,
            "amount_usd": amount_usd,
            "plan_name": plan_name,
            "message": "STK Push sent to your phone. Please enter your PIN to complete payment."
        }
    except HTTPException as he:
        raise he
    except Exception:
        logger.exception("Unexpected PesaFlux initiation error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to initiate payment. Please try again later.",
        )


@router.post("/submit-review/{ref}")
async def submit_deposit_for_review(
    ref: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: models.User = Depends(get_current_user),
):
    """Submit an M-Pesa recharge attempt to the admin queue after PIN entry."""
    result = await db.execute(
        select(PesaFluxPayment).filter(
            PesaFluxPayment.reference == ref,
            PesaFluxPayment.user_id == current_user.id,
        )
    )
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment reference not found.")
    if payment.payment_type != "recharge" or payment.plan_id is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only M-Pesa deposits can be submitted for manual review.")

    existing = await db.execute(
        select(models.Payment).filter(
            models.Payment.user_id == current_user.id,
            models.Payment.type == "deposit",
            models.Payment.payment_method == "M-Pesa (Manual Review)",
            models.Payment.admin_notes.like(f"%Reference: {ref}.%"),
        )
    )
    review = existing.scalars().first()
    if review:
        return {"id": review.id, "status": review.status, "message": "This deposit is already under review."}

    review = await _create_manual_deposit_review(
        payment,
        db,
        "User confirmed PIN entry and submitted the attempt for administrator verification",
    )
    return {
        "id": review.id,
        "status": review.status,
        "message": "Your deposit is now under review. An administrator will verify it before crediting your wallet.",
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
    Check the status of a PesaFlux payment attempt.
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
            detail="Payment reference not found."
        )

    # If already completed or failed, return cached status
    if payment.status != "pending":
        plan_name = None
        if payment.plan_id:
            plan_res = await db.execute(select(models.Plan).filter(models.Plan.id == payment.plan_id))
            plan = plan_res.scalar_one_or_none()
            plan_name = plan.name if plan else "Unknown Plan"
        
        msg = ""
        if payment.status == "completed":
            msg = f"Payment of ${payment.amount_usd:.2f} via M-Pesa was successful."
        elif payment.status == "failed":
            msg = "Payment was not completed."
        
        return {
            "reference": payment.reference,
            "status": payment.status,
            "plan_name": plan_name,
            "amount_usd": payment.amount_usd,
            "amount_kes": payment.amount,
            "mpesa_receipt": payment.mpesa_receipt,
            "message": msg,
        }

    # Otherwise, check with PesaFlux (sync check)
    if not payment.transaction_request_id:
        return {
            "reference": payment.reference,
            "status": "pending",
            "plan_name": None,
            "amount_usd": payment.amount_usd,
            "amount_kes": payment.amount,
            "mpesa_receipt": None,
            "message": "",
        }
    
    status_res = await pesaflux_service.get_payment_status(payment.transaction_request_id)
    
    # Confirm the provider response matches our own pending record before any
    # wallet credit or plan activation is allowed.
    if status_res["success"] and status_res["status"] == "completed":
        reported_reference = status_res.get("transaction_reference")
        reported_amount = status_res.get("transaction_amount")
        if (
            (not _internal_reference_matches(reported_reference, payment.reference))
            or (reported_amount is not None and not _amount_matches(reported_amount, payment.amount))
        ):
            logger.error("PesaFlux status mismatch for payment reference=%s", payment.reference)
            return {
                "reference": payment.reference,
                "status": "pending",
                "plan_name": None,
                "amount_usd": payment.amount_usd,
                "amount_kes": payment.amount,
                "mpesa_receipt": None,
                "message": "",
            }
        await _process_successful_payment(payment, db, status_res)
        # Get plan_name from local DB (provider doesn't return it)
        local_plan_name = None
        if payment.plan_id:
            plan_res = await db.execute(select(models.Plan).filter(models.Plan.id == payment.plan_id))
            local_plan = plan_res.scalar_one_or_none()
            local_plan_name = local_plan.name if local_plan else None
        return {
            "reference": payment.reference,
            "status": "completed",
            "plan_name": local_plan_name,
            "amount_usd": payment.amount_usd,
            "amount_kes": payment.amount,
            "mpesa_receipt": payment.mpesa_receipt,
            "message": f"Payment of ${payment.amount_usd:.2f} via M-Pesa was successful.",
        }
    
    # If PesaFlux says it failed
    if status_res["success"] and status_res["status"] == "failed":
        payment.status = "failed"
        await db.commit()
        return {
            "reference": payment.reference,
            "status": "failed",
            "plan_name": None,
            "amount_usd": payment.amount_usd,
            "amount_kes": payment.amount,
            "mpesa_receipt": None,
            "message": "Payment was not completed.",
        }

    # Still pending
    return {
        "reference": payment.reference,
        "status": "pending",
        "plan_name": None,
        "amount_usd": payment.amount_usd,
        "amount_kes": payment.amount,
        "mpesa_receipt": None,
        "message": "",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint: PesaFlux Webhook
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/webhook")
async def pesaflux_webhook(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
):
    """Acknowledge legacy callbacks without changing wallet or plan state.

    M-Pesa deposits are deliberately reviewed through the admin payments queue.
    """
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON")

    # The provider documents TransactionReference/ResponseCode. The lowercase
    # fallbacks retain compatibility with the previously deployed callback shape.
    reference = data.get("TransactionReference") or data.get("reference")
    if not reference:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing transaction reference")

    result = await db.execute(select(PesaFluxPayment).filter(PesaFluxPayment.reference == reference))
    payment = result.scalar_one_or_none()
    if not payment:
        logger.warning("PesaFlux callback referenced an unknown payment")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payment reference not found")
    # Manual-review deposits must never be auto-processed by a callback. This
    # also makes late callbacks harmless after an administrator has acted.
    if payment.payment_type == "recharge" or payment.plan_id is None:
        return {"status": "accepted", "message": "Callback acknowledged; awaiting user confirmation for manual review"}
    if payment.status != "pending":
        return {"status": "success", "message": "Already processed"}

    callback_amount = data.get("TransactionAmount") or data.get("amount")
    callback_phone = data.get("Msisdn") or data.get("phone")
    if callback_amount is not None and not _amount_matches(callback_amount, payment.amount):
        logger.error("PesaFlux callback amount mismatch for payment reference=%s", payment.reference)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Callback amount mismatch")
    if callback_phone and _normalize_phone(str(callback_phone)) != payment.phone:
        logger.error("PesaFlux callback phone mismatch for payment reference=%s", payment.reference)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Callback phone mismatch")

    response_code = str(data.get("ResponseCode", "")).strip()
    reported_status = str(data.get("status", data.get("TransactionStatus", ""))).lower()
    is_success = response_code in {"0", "200"} or reported_status in {"completed", "success"}
    is_failure = bool(response_code) and response_code not in {"0", "200"}

    if is_success:
        callback_verified = {
            "success": True,
            "status": "completed",
            "transaction_id": data.get("TransactionID"),
            "mpesa_receipt": data.get("TransactionReceipt"),
            "transaction_reference": reference,
            "transaction_amount": callback_amount,
            "phone": callback_phone,
        }
        if payment.transaction_request_id:
            verified = await pesaflux_service.get_payment_status(payment.transaction_request_id)
            if not verified.get("success") or verified.get("status") != "completed":
                # PesaFlux has already delivered a successful, amount- and
                # phone-matched callback. Its status endpoint can briefly lag;
                # do not leave a paid deposit pending because of that race.
                logger.warning("Using successful PesaFlux callback while status endpoint is not final for %s", payment.reference)
                verified = callback_verified
        else:
            # A very fast provider callback can arrive before the initiation
            # response has committed transaction_request_id. The callback has
            # already been validated against our reference, amount, and phone;
            # preserve it as the provider confirmation instead of dropping it.
            logger.warning("Processing PesaFlux callback before request id was persisted")
            verified = callback_verified
        if (
            not _internal_reference_matches(verified.get("transaction_reference"), payment.reference)
            or (verified.get("transaction_amount") is not None and not _amount_matches(verified["transaction_amount"], payment.amount))
        ):
            logger.error("PesaFlux verification mismatch for payment reference=%s", payment.reference)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Provider verification mismatch")
        await _process_successful_payment(payment, db, verified)
        return {"status": "success", "message": "Payment processed"}

    if is_failure:
        payment.status = "failed"
        await db.commit()
        return {"status": "success", "message": "Payment marked as failed"}

    return {"status": "accepted", "message": "Callback received; awaiting final status"}


# ─────────────────────────────────────────────────────────────────────────────
# Internal: Success Processor
# ─────────────────────────────────────────────────────────────────────────────

async def _process_successful_payment(payment: PesaFluxPayment, db: AsyncSession, provider_data: dict):
    """
    Side effects of a successful payment:
    1. Update payment record
    2. If plan_id: Activate plan (purchase or upgrade)
    3. If no plan_id: Credit deposit wallet (recharge)
    4. Record in main payments history
    """
    if payment.status == "completed":
        return

    # 1. Update payment record
    payment.status = "completed"
    payment.provider_transaction_id = provider_data.get("transaction_id") or provider_data.get("TransactionID")
    payment.mpesa_receipt = provider_data.get("mpesa_receipt") or provider_data.get("TransactionReceipt")
    payment.completed_at = _utc_now()
    
    # 2. Fetch user
    user_res = await db.execute(select(models.User).filter(models.User.id == payment.user_id))
    user = user_res.scalar_one()

    # 3. Handle Logic
    if payment.payment_type == "recharge" or not payment.plan_id:
        # PURE RECHARGE
        user.deposit_wallet_balance = (user.deposit_wallet_balance or 0.0) + payment.amount_usd
        logger.info("User %s recharged $%s via M-Pesa", user.id, payment.amount_usd)
    else:
        # PLAN PURCHASE OR UPGRADE
        if payment.plan_activated == "no":
            plan_res = await db.execute(select(models.Plan).filter(models.Plan.id == payment.plan_id))
            plan = plan_res.scalar_one()
            
            # Record old plan for history if upgrading
            old_plan_id = user.current_plan_id
            now = _utc_now()
            
            # Mark old plan history as upgraded (parity with /plans/upgrade endpoint)
            if payment.payment_type == "upgrade" and old_plan_id:
                old_history_res = await db.execute(
                    select(models.UserPlanHistory)
                    .filter(
                        models.UserPlanHistory.user_id == user.id,
                        models.UserPlanHistory.plan_id == old_plan_id,
                        models.UserPlanHistory.status == "active",
                    )
                    .order_by(models.UserPlanHistory.purchased_at.desc())
                )
                old_history = old_history_res.scalars().first()
                if old_history:
                    old_history.status = "upgraded"
                    db.add(old_history)

            # Update user plan
            user.current_plan_id = plan.id
            user.plan_purchase_price = plan.price
            user.plan_start_date = now
            user.plan_expiry_date = now + timedelta(days=plan.validity_days)
            user.has_purchased_first_package = True
            
            # For STK-based plan purchases/upgrades, the user pays directly via M-Pesa.
            # The deposit wallet is NOT deducted (the user already paid externally).
            # For upgrades, credit the old plan price to withdrawal wallet immediately.
            if payment.payment_type == "upgrade" and old_plan_id:
                old_plan_res = await db.execute(select(models.Plan).filter(models.Plan.id == old_plan_id))
                old_plan = old_plan_res.scalar_one_or_none()
                if old_plan:
                    refund_amount = old_plan.price
                    user.withdrawal_wallet_balance = (user.withdrawal_wallet_balance or 0.0) + refund_amount
                    # Log the refund to EarningsLog for period tracking
                    db.add(models.EarningsLog(
                        user_id=user.id,
                        amount=refund_amount,
                        type="upgrade_refund",
                        description="Immediate upgrade refund for previous plan (M-Pesa)"
                    ))
                    # Audit trail in upgrade_refunds table
                    db.add(models.UpgradeRefund(
                        user_id=user.id,
                        amount=refund_amount,
                        status="released",
                        release_at=now,
                        released_at=now,
                    ))
                    logger.info("User %s upgraded via STK: credited $%s old plan price to withdrawal", user.id, refund_amount)

            # Clean up old plan's pending tasks (parity with /plans/upgrade endpoint)
            if payment.payment_type == "upgrade" and old_plan_id:
                await db.execute(
                    models.UserVideoTask.__table__.delete().where(
                        models.UserVideoTask.user_id == user.id,
                        models.UserVideoTask.status == "pending",
                    )
                )

            # Intern receives only Intern tasks; all other plans receive global and plan-specific tasks.
            new_task_filter = (models.VideoTask.plan_id == plan.id) if plan.name.strip().lower() == "intern" else (models.VideoTask.plan_id.is_(None)) | (models.VideoTask.plan_id == plan.id)
            new_tasks_res = await db.execute(
                select(models.VideoTask).filter(new_task_filter)
            )
            for task in new_tasks_res.scalars().all():
                existing_res = await db.execute(
                    select(models.UserVideoTask).filter(
                        models.UserVideoTask.user_id == user.id,
                        models.UserVideoTask.video_task_id == task.id,
                    )
                )
                if not existing_res.scalar_one_or_none():
                    db.add(models.UserVideoTask(
                        user_id=user.id,
                        video_task_id=task.id,
                        status="pending",
                    ))

            # Add to UserPlanHistory
            history = models.UserPlanHistory(
                user_id=user.id,
                plan_id=plan.id,
                purchase_price=plan.price,
                expires_at=user.plan_expiry_date,
                status="active",
                pesaflux_payment_id=payment.id
            )
            db.add(history)
            
            # Mark as activated
            payment.plan_activated = "yes"
            logger.info("User %s activated plan %s via M-Pesa", user.id, plan.name)

    # 4. Add to main Payment history table for UI visibility
    history_payment = models.Payment(
        user_id=user.id,
        amount=payment.amount_usd,
        status="completed",
        type="deposit",
        payment_method="M-Pesa (PesaFlux)",
        proof_url=f"Receipt: {payment.mpesa_receipt or 'N/A'}"
    )
    db.add(history_payment)

    await db.commit()

    # Invalidate user cache so tasks, dashboard, and plan state refresh immediately
    # after a successful M-Pesa payment (purchase, upgrade, or recharge).
    await invalidate_user_cache(user.id, "tasks", "dashboard", "referrals", "payments")
    await invalidate_shared_cache("admin_stats")
