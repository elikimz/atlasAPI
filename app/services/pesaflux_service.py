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
        dict with keys:
            success (bool)
            transaction_request_id (str|None)
            error (str|None)
            error_code (str|None): 'config_missing' | 'provider_error' | 'timeout' | 'network_error'
    """
    if not settings.PESAFLUX_API_KEY or not settings.PESAFLUX_EMAIL:
        logger.error(
            "PesaFlux credentials not configured. "
            "Set PESAFLUX_API_KEY and PESAFLUX_EMAIL in Azure App Service environment variables."
        )
        return {
            "success": False,
            "transaction_request_id": None,
            "error": "M-Pesa payment is temporarily unavailable. Please try again later or use Crypto (USDT) to recharge.",
            "error_code": "config_missing"
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

        logger.info(
            "PesaFlux STK raw response for reference=%s: status_code=%s body=%s",
            reference, response.status_code, response.text[:500]
        )

        # Handle non-200 HTTP responses from PesaFlux
        if response.status_code >= 500:
            logger.error(
                "PesaFlux API server error for reference=%s: status_code=%s body=%s",
                reference, response.status_code, response.text[:200]
            )
            return {
                "success": False,
                "transaction_request_id": None,
                "error": f"M-Pesa payment provider is temporarily unavailable. (Status {response.status_code}). Please try again in a few minutes.",
                "error_code": "provider_error"
            }

        if response.status_code == 401 or response.status_code == 403:
            logger.error(
                "PesaFlux API authentication failed for reference=%s: status_code=%s body=%s",
                reference, response.status_code, response.text[:200]
            )
            return {
                "success": False,
                "transaction_request_id": None,
                "error": f"M-Pesa payment authentication failed. (Status {response.status_code}). Please contact support.",
                "error_code": "auth_error"
            }
            
        if response.status_code >= 400:
            logger.error(
                "PesaFlux API client error for reference=%s: status_code=%s body=%s",
                reference, response.status_code, response.text[:200]
            )
            return {
                "success": False,
                "transaction_request_id": None,
                "error": f"M-Pesa payment failed with provider error. (Status {response.status_code}). Please check your details.",
                "error_code": "provider_error"
            }

        try:
            data = response.json()
        except Exception as e:
            logger.error(
                "PesaFlux API returned non-JSON response for reference=%s: %s | Error: %s",
                reference, response.text[:200], str(e)
            )
            return {
                "success": False,
                "transaction_request_id": None,
                "error": "Unexpected response from M-Pesa provider. Please try again.",
                "error_code": "provider_error"
            }

        logger.info(
            "PesaFlux STK initiation response for reference=%s: success=%s txn_id=%s",
            reference, data.get("success"), data.get("transaction_request_id")
        )

        # PesaFlux returns {"success": "200", "massage": "...", "transaction_request_id": "..."}
        # Note: PesaFlux uses "massage" (typo) instead of "message"
        if str(data.get("success")) == "200" and data.get("transaction_request_id"):
            return {
                "success": True,
                "transaction_request_id": data["transaction_request_id"],
                "error": None,
                "error_code": None
            }
        else:
            # Extract error message from PesaFlux response (handles their typo "massage")
            # PesaFlux documented error response uses ResponseCode and ResponseDescription
            provider_error = (
                data.get("ResponseDescription")
                or data.get("massage")
                or data.get("message")
                or data.get("error")
                or data.get("description")
                or "STK Push initiation failed."
            )
            
            # Map specific PesaFlux error codes to actionable error codes
            response_code = data.get("ResponseCode")
            error_code = "provider_error"
            if response_code == 1032:
                error_code = "user_cancelled"
            elif response_code == 1037:
                error_code = "subscriber_unreachable"
            elif response_code == 1:
                error_code = "insufficient_balance"
            
            logger.warning(
                "PesaFlux STK initiation failed for reference=%s: %s | full_response=%s",
                reference, provider_error, data
            )
            
            # Format a user-friendly error message
            user_msg = f"M-Pesa payment failed: {provider_error}"
            if response_code == 1:
                user_msg = f"M-Pesa payment failed: Insufficient balance. Please check your M-Pesa balance and try again."
            elif response_code == 1037:
                user_msg = f"M-Pesa payment failed: Unable to reach subscriber. Please ensure your phone is on and has network coverage."
            
            return {
                "success": False,
                "transaction_request_id": None,
                "error": user_msg,
                "error_code": error_code
            }

    except httpx.TimeoutException:
        logger.error("PesaFlux STK Push timed out for reference=%s", reference)
        return {
            "success": False,
            "transaction_request_id": None,
            "error": "M-Pesa payment request timed out. Please check your connection and try again.",
            "error_code": "timeout"
        }
    except httpx.ConnectError as exc:
        logger.error("PesaFlux STK Push connection error for reference=%s: %s", reference, str(exc))
        return {
            "success": False,
            "transaction_request_id": None,
            "error": "Unable to connect to M-Pesa payment provider. Please try again later.",
            "error_code": "network_error"
        }
    except Exception as exc:
        logger.error("PesaFlux STK Push unexpected error for reference=%s: %s", reference, str(exc), exc_info=True)
        return {
            "success": False,
            "transaction_request_id": None,
            "error": "An unexpected error occurred while initiating M-Pesa payment. Please try again.",
            "error_code": "unknown_error"
        }


async def get_payment_status(reference: str) -> dict:
    """
    Check the status of a PesaFlux transaction by its reference.

    Args:
        reference: The unique payment reference

    Returns:
        dict with keys:
            success (bool)
            status (str): "completed" | "failed" | "pending"
            mpesa_receipt (str|None)
            error (str|None)
    """
    if not settings.PESAFLUX_API_KEY or not settings.PESAFLUX_EMAIL:
        return {"success": False, "status": "pending", "error": "Config missing"}

    payload = {
        "api_key": settings.PESAFLUX_API_KEY,
        "email": settings.PESAFLUX_EMAIL,
        "reference": reference
    }

    try:
        async with httpx.AsyncClient(timeout=PESAFLUX_TIMEOUT) as client:
            response = await client.post(
                PESAFLUX_STATUS_URL,
                json=payload,
                headers={"Content-Type": "application/json"}
            )

        if response.status_code != 200:
            return {"success": False, "status": "pending", "error": "Provider HTTP error"}

        data = response.json()
        
        # PesaFlux returns "Completed", "Failed", "Pending"
        raw_status = data.get("TransactionStatus", data.get("ResponseDescription", "Pending")).lower()
        
        # Map to our internal status
        final_status = "pending"
        if raw_status == "completed" or raw_status == "success":
            final_status = "completed"
        elif raw_status == "failed" or "cancelled" in raw_status:
            final_status = "failed"

        return {
            "success": True,
            "status": final_status,
            "mpesa_receipt": data.get("TransactionReceipt") or data.get("TransactionID"),
            "error": None
        }
    except Exception as e:
        logger.error(f"PesaFlux status check error: {e}")
        return {"success": False, "status": "pending", "error": str(e)}
