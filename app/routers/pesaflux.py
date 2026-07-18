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
            amount_kes=pf_payment.amount,
            reference=pf_payment.reference
        )

        if not init_res["success"]:
            # Update record as failed immediately
            pf_payment.status = "failed"
            await db.commit()
            
            # Handle errors from pesaflux_service
            error_msg = init_res.get("error") or init_res.get("message") or "Failed to initiate M-Pesa payment. Please try again later."
            error_code = init_res.get("error_code")
            
            logger.error(
                "PesaFlux STK Push failed for reference=%s: code=%s msg=%s",
                reference, error_code, error_msg
            )
            
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=error_msg
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
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"FATAL ERROR in /pesaflux/initiate: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal Server Error: {str(e)}"
        )


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
        
        return {
            "reference": payment.reference,
            "status": payment.status,
            "plan_name": plan_name,
            "amount_usd": payment.amount_usd
        }

    # Otherwise, check with PesaFlux (sync check)
    if not payment.transaction_request_id:
        return {
            "reference": payment.reference,
            "status": "pending",
            "plan_name": None,
            "amount_usd": payment.amount_usd
        }
    
    status_res = await pesaflux_service.get_payment_status(payment.transaction_request_id)
    
    # If PesaFlux says it's completed, process it
    if status_res["success"] and status_res["status"] == "completed":
        await _process_successful_payment(payment, db, status_res)
        return {
            "reference": payment.reference,
            "status": "completed",
            "plan_name": status_res.get("plan_name"),
            "amount_usd": payment.amount_usd
        }
    
    # If PesaFlux says it failed
    if status_res["success"] and status_res["status"] == "failed":
        payment.status = "failed"
        await db.commit()
        return {
            "reference": payment.reference,
            "status": "failed",
            "plan_name": None,
            "amount_usd": payment.amount_usd
        }

    # Still pending
    return {
        "reference": payment.reference,
        "status": "pending",
        "plan_name": None,
        "amount_usd": payment.amount_usd
    }


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint: PesaFlux Webhook
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/webhook")
async def pesaflux_webhook(
    request: Request,
    db: AsyncSession = Depends(get_async_db)
):
    """
    PesaFlux Webhook Handler.
    PesaFlux POSTs JSON here when a transaction is completed/failed.
    """
    try:
        data = await request.json()
    except Exception:
        return {"status": "error", "message": "Invalid JSON"}

    logger.info("PesaFlux Webhook received: %s", data)

    # PesaFlux Webhook fields:
    # reference, transaction_id, status, mpesa_receipt, amount, ...
    ref = data.get("reference")
    if not ref:
        return {"status": "error", "message": "Missing reference"}

    result = await db.execute(
        select(PesaFluxPayment).filter(PesaFluxPayment.reference == ref)
    )
    payment = result.scalar_one_or_none()
    if not payment:
        logger.warning("PesaFlux Webhook: Reference %s not found in DB", ref)
        return {"status": "error", "message": "Reference not found"}

    if payment.status != "pending":
        return {"status": "success", "message": "Already processed"}

    status_str = str(data.get("status")).lower()
    if status_str == "completed" or status_str == "success" or status_str == "200":
        await _process_successful_payment(payment, db, data)
        return {"status": "success", "message": "Payment processed"}
    else:
        payment.status = "failed"
        await db.commit()
        return {"status": "success", "message": "Payment marked as failed"}


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
    payment.provider_transaction_id = provider_data.get("transaction_id")
    payment.mpesa_receipt = provider_data.get("mpesa_receipt")
    payment.completed_at = _utc_now()
    
    # 2. Fetch user
    user_res = await db.execute(select(models.User).filter(models.User.id == payment.user_id))
    user = user_res.scalar_one()

    # 3. Handle Logic
    if payment.payment_type == "recharge" or not payment.plan_id:
        # PURE RECHARGE
        user.deposit_wallet_balance += payment.amount_usd
        logger.info("User %s recharged $%s via M-Pesa", user.id, payment.amount_usd)
    else:
        # PLAN PURCHASE OR UPGRADE
        if payment.plan_activated == "no":
            plan_res = await db.execute(select(models.Plan).filter(models.Plan.id == payment.plan_id))
            plan = plan_res.scalar_one()
            
            # Record old plan for history if upgrading
            old_plan_id = user.current_plan_id
            
            # Update user plan
            user.current_plan_id = plan.id
            user.plan_purchase_price = plan.price
            user.plan_start_date = _utc_now()
            user.plan_expiry_date = _utc_now() + timedelta(days=plan.validity_days)
            user.has_purchased_first_package = True
            
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
