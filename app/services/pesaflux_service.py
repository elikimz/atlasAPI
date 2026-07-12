"""
PesaFlux M-Pesa STK Push API service.

This is a NEW, ISOLATED service file.
It communicates with the PesaFlux API on behalf of the backend.
The PesaFlux API key is NEVER exposed to the frontend.

PesaFlux API documentation: https://api.pesaflux.co.ke/documentation/
"""

import httpx
import logging
from app.config import settings

logger = logging.getLogger(__name__)

PESAFLUX_STK_URL = "https://api.pesaflux.co.ke/v1/initiatestk"
PESAFLUX_STATUS_URL = "https://api.pesaflux.co.ke/v1/transactionstatus"

# Timeout for PesaFlux API calls (seconds)
PESAFLUX_TIMEOUT = 30


async def initiate_stk_push(
    phone: str,
    amount_kes: int,
    reference: str
) -> dict:
    """
    Initiate an M-Pesa STK Push via PesaFlux.

    Args:
        phone: Phone number in format 2547XXXXXXXX or 07XXXXXXXX
        amount_kes: Amount in KES (integer)
        reference: Unique payment reference (our internal reference)

    Returns:
        dict with keys: success (bool), transaction_request_id (str|None), error (str|None)
    """
    if not settings.PESAFLUX_API_KEY or not settings.PESAFLUX_EMAIL:
        logger.error("PesaFlux credentials not configured in environment variables.")
        return {
            "success": False,
            "transaction_request_id": None,
            "error": "Payment provider not configured. Please contact support."
        }

    payload = {
        "api_key": settings.PESAFLUX_API_KEY,
        "email": settings.PESAFLUX_EMAIL,
        "amount": str(amount_kes),
        "msisdn": phone,
        "reference": reference
    }

    try:
        async with httpx.AsyncClient(timeout=PESAFLUX_TIMEOUT) as client:
            response = await client.post(
                PESAFLUX_STK_URL,
                json=payload,
                headers={"Content-Type": "application/json"}
            )

        data = response.json()
        logger.info(
            "PesaFlux STK initiation response for reference=%s: status=%s",
            reference, data.get("success")
        )

        # PesaFlux returns {"success": "200", "massage": "...", "transaction_request_id": "..."}
        if str(data.get("success")) == "200" and data.get("transaction_request_id"):
            return {
                "success": True,
                "transaction_request_id": data["transaction_request_id"],
                "error": None
            }
        else:
            return {
                "success": False,
                "transaction_request_id": None,
                "error": data.get("massage") or data.get("message") or "STK Push initiation failed."
            }

    except httpx.TimeoutException:
        logger.error("PesaFlux STK Push timed out for reference=%s", reference)
        return {
            "success": False,
            "transaction_request_id": None,
            "error": "Payment provider timed out. Please try again."
        }
    except Exception as exc:
        logger.error("PesaFlux STK Push error for reference=%s: %s", reference, str(exc))
        return {
            "success": False,
            "transaction_request_id": None,
            "error": "Payment provider error. Please try again."
        }


async def check_transaction_status(transaction_request_id: str) -> dict:
    """
    Check the status of a PesaFlux transaction.

    Args:
        transaction_request_id: The transaction_request_id returned by initiate_stk_push

    Returns:
        dict with keys:
            success (bool)
            status (str): "Completed" | "Failed" | "Pending" | "Unknown"
            receipt (str|None): M-Pesa receipt number
            amount (str|None): Amount transacted
            phone (str|None): Phone number
            reference (str|None): Transaction reference
            error (str|None)
    """
    if not settings.PESAFLUX_API_KEY or not settings.PESAFLUX_EMAIL:
        return {
            "success": False,
            "status": "Unknown",
            "receipt": None,
            "amount": None,
            "phone": None,
            "reference": None,
            "error": "Payment provider not configured."
        }

    payload = {
        "api_key": settings.PESAFLUX_API_KEY,
        "email": settings.PESAFLUX_EMAIL,
        "transaction_request_id": transaction_request_id
    }

    try:
        async with httpx.AsyncClient(timeout=PESAFLUX_TIMEOUT) as client:
            response = await client.post(
                PESAFLUX_STATUS_URL,
                json=payload,
                headers={"Content-Type": "application/json"}
            )

        data = response.json()
        logger.info(
            "PesaFlux status check for txn_id=%s: status=%s",
            transaction_request_id, data.get("TransactionStatus")
        )

        return {
            "success": True,
            "status": data.get("TransactionStatus", "Unknown"),
            "receipt": data.get("TransactionReceipt"),
            "amount": data.get("TransactionAmount"),
            "phone": data.get("Msisdn"),
            "reference": data.get("TransactionReference"),
            "result_code": data.get("TransactionCode"),
            "result_desc": data.get("ResultDesc"),
            "error": None
        }

    except httpx.TimeoutException:
        logger.error("PesaFlux status check timed out for txn_id=%s", transaction_request_id)
        return {
            "success": False,
            "status": "Unknown",
            "receipt": None,
            "amount": None,
            "phone": None,
            "reference": None,
            "error": "Status check timed out."
        }
    except Exception as exc:
        logger.error("PesaFlux status check error for txn_id=%s: %s", transaction_request_id, str(exc))
        return {
            "success": False,
            "status": "Unknown",
            "receipt": None,
            "amount": None,
            "phone": None,
            "reference": None,
            "error": "Status check failed."
        }
