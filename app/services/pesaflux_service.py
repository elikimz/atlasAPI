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
                "PesaFlux API server error for reference=%s: status_code=%s",
                reference, response.status_code
            )
            return {
                "success": False,
                "transaction_request_id": None,
                "error": "M-Pesa payment provider is temporarily unavailable. Please try again in a few minutes.",
                "error_code": "provider_error"
            }

        if response.status_code == 401 or response.status_code == 403:
            logger.error(
                "PesaFlux API authentication failed for reference=%s: status_code=%s",
                reference, response.status_code
            )
            return {
                "success": False,
                "transaction_request_id": None,
                "error": "M-Pesa payment authentication failed. Please contact support.",
                "error_code": "auth_error"
            }

        try:
            data = response.json()
        except Exception:
            logger.error(
                "PesaFlux API returned non-JSON response for reference=%s: %s",
                reference, response.text[:200]
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
            provider_error = (
                data.get("massage")
                or data.get("message")
                or data.get("error")
                or data.get("description")
                or "STK Push initiation failed."
            )
            logger.warning(
                "PesaFlux STK initiation failed for reference=%s: %s | full_response=%s",
                reference, provider_error, data
            )
            return {
                "success": False,
                "transaction_request_id": None,
                "error": f"M-Pesa payment failed: {provider_error}. Please check your phone number and try again.",
                "error_code": "provider_error"
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

        logger.info(
            "PesaFlux status check raw response for txn_id=%s: status_code=%s body=%s",
            transaction_request_id, response.status_code, response.text[:300]
        )

        try:
            data = response.json()
        except Exception:
            logger.error(
                "PesaFlux status check returned non-JSON for txn_id=%s: %s",
                transaction_request_id, response.text[:200]
            )
            return {
                "success": False,
                "status": "Unknown",
                "receipt": None,
                "amount": None,
                "phone": None,
                "reference": None,
                "error": "Unexpected response from payment provider."
            }

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
    except httpx.ConnectError as exc:
        logger.error("PesaFlux status check connection error for txn_id=%s: %s", transaction_request_id, str(exc))
        return {
            "success": False,
            "status": "Unknown",
            "receipt": None,
            "amount": None,
            "phone": None,
            "reference": None,
            "error": "Unable to connect to payment provider."
        }
    except Exception as exc:
        logger.error("PesaFlux status check unexpected error for txn_id=%s: %s", transaction_request_id, str(exc), exc_info=True)
        return {
            "success": False,
            "status": "Unknown",
            "receipt": None,
            "amount": None,
            "phone": None,
            "reference": None,
            "error": "Status check failed."
        }
