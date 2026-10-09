from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage
from html import escape

from app.config import settings

logger = logging.getLogger(__name__)


def _build_message(
    *,
    recipient: str,
    first_name: str | None,
    amount: float,
    status_label: str,
    status_color: str,
    status_message: str,
    details: str | None,
    title: str = "Withdrawal update",
) -> EmailMessage:
    name = (first_name or "there").strip() or "there"
    safe_name = escape(name)
    safe_status = escape(status_label)
    safe_message = escape(status_message)
    safe_details = escape(details.strip()) if details and details.strip() else ""

    subject = title
    text_lines = [
        f"Dear {name},",
        "",
        status_message,
        "",
        f"STATUS: {status_label}",
    ]
    if details and details.strip():
        text_lines.extend([f"Admin note: {details.strip()}"])
    text_lines.extend(["", "Thank you,", "AdPulseAI Support"])

    details_block = (
        f'<div style="margin:20px 0;padding:14px 16px;border-left:4px solid #{status_color};'
        f'background:#f6f7f7;color:#3c434a;font-size:14px;line-height:1.6;">'
        f'<strong>Admin note:</strong> {safe_details}</div>'
        if safe_details
        else ""
    )
    html = f"""<!doctype html>
<html lang="en">
  <body style="margin:0;background:#f0f0f1;font-family:Arial,Helvetica,sans-serif;color:#1d2327;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f0f0f1;padding:32px 12px;">
      <tr><td align="center">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border:1px solid #dcdcde;">
          <tr><td style="padding:24px 28px;background:#2271b1;color:#ffffff;">
            <div style="font-size:22px;font-weight:700;letter-spacing:.2px;">AdPulseAI</div>
            <div style="margin-top:4px;font-size:13px;opacity:.9;">{escape(title)}</div>
          </td></tr>
          <tr><td style="padding:28px;">
            <p style="margin:0 0 16px;font-size:16px;">Dear {safe_name},</p>
            <p style="margin:0 0 22px;font-size:15px;line-height:1.6;color:#50575e;">{safe_message}</p>
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #dcdcde;background:#f6f7f7;">
              <tr><td style="padding:14px 16px;font-size:13px;color:#646970;">Withdrawal amount</td><td align="right" style="padding:14px 16px;font-size:17px;font-weight:700;color:#1d2327;">${amount:,.2f}</td></tr>
              <tr><td style="padding:14px 16px;border-top:1px solid #dcdcde;font-size:13px;color:#646970;">Status</td><td align="right" style="padding:14px 16px;border-top:1px solid #dcdcde;font-size:14px;font-weight:700;color:#{status_color};">{safe_status}</td></tr>
            </table>
            {details_block}
            <p style="margin:24px 0 0;font-size:13px;line-height:1.6;color:#646970;">If you did not request this, please contact AdPulseAI Support immediately.</p>
          </td></tr>
          <tr><td style="padding:18px 28px;border-top:1px solid #dcdcde;font-size:12px;color:#8c8f94;">© 2026 AdPulseAI. All rights reserved.</td></tr>
        </table>
      </td></tr>
    </table>
  </body>
</html>"""

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.EMAIL_SENDER or "AdPulseAI"
    message["To"] = recipient
    message.set_content("\n".join(text_lines))
    message.add_alternative(html, subtype="html")
    return message


def _send_sync(message: EmailMessage) -> None:
    if not settings.EMAIL_SENDER or not settings.EMAIL_APP_PASSWORD:
        raise RuntimeError("EMAIL_SENDER and EMAIL_APP_PASSWORD are required to send email")

    if settings.SMTP_PORT == 465:
        with smtplib.SMTP_SSL(settings.SMTP_SERVER, settings.SMTP_PORT, timeout=20) as smtp:
            smtp.login(settings.EMAIL_SENDER, settings.EMAIL_APP_PASSWORD)
            smtp.send_message(message)
        return

    with smtplib.SMTP(settings.SMTP_SERVER, settings.SMTP_PORT, timeout=20) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(settings.EMAIL_SENDER, settings.EMAIL_APP_PASSWORD)
        smtp.send_message(message)


async def send_withdrawal_status_email(
    *,
    recipient: str | None,
    first_name: str | None,
    amount: float,
    approved: bool,
    details: str | None = None,
) -> bool:
    """Send a withdrawal decision email without making the API decision fail."""
    if not recipient or "@" not in recipient:
        logger.warning("Skipping withdrawal email: user has no valid email address")
        return False

    status_label = "APPROVED" if approved else "CANCELED"
    status_color = "008a20" if approved else "b32d2e"
    status_message = (
        f"Congratulations! Your withdrawal request has been approved and sent to your designated wallet. "
        f"Amount: ${amount:,.2f}. Status: APPROVED."
        if approved
        else f"Your withdrawal was canceled and the amount of ${amount:,.2f} has been returned to your withdrawal wallet. Status: CANCELED."
    )
    message = _build_message(
        recipient=recipient,
        first_name=first_name,
        amount=amount,
        status_label=status_label,
        status_color=status_color,
        status_message=status_message,
        details=details,
    )

    try:
        await asyncio.to_thread(_send_sync, message)
        logger.info("Withdrawal status email sent to user %s", recipient)
        return True
    except Exception:
        logger.exception("Could not send withdrawal status email to user %s", recipient)
        return False


async def send_withdrawal_request_received_email(
    *,
    recipient: str | None,
    full_name: str | None,
    amount: float,
) -> bool:
    """Send the initial processing confirmation after a withdrawal is recorded."""
    message = _build_message(
        recipient=recipient or "",
        first_name=full_name,
        amount=amount,
        status_label="PROCESSING",
        status_color="2271b1",
        status_message=(
            f"We are pleased to inform you that we have successfully received your "
            f"${amount:,.2f} withdrawal request. Your request has been recorded in our "
            "system and is currently undergoing the necessary processing and verification "
            "procedures. Please allow the required processing time for the transaction to "
            "be completed. Once finalized, the funds will be released through your selected "
            "payment method. No further action is required from you at this time."
        ),
        details=None,
        title="Withdrawal Request Received",
    ) if recipient and "@" in recipient else None

    if message is None:
        logger.warning("Skipping withdrawal received email: user has no valid email address")
        return False
    try:
        await asyncio.to_thread(_send_sync, message)
        logger.info("Withdrawal received email sent to user %s", recipient)
        return True
    except Exception:
        logger.exception("Could not send withdrawal received email to user %s", recipient)
        return False


async def send_password_reset_code_email(
    *,
    recipient: str | None,
    first_name: str | None,
    code: str,
) -> bool:
    """Send a short-lived one-time password reset code."""
    if not recipient or "@" not in recipient:
        logger.warning("Skipping password reset email: user has no valid email address")
        return False

    name = escape((first_name or "there").strip() or "there")
    safe_code = escape(code)
    message = EmailMessage()
    message["Subject"] = "Your AdPulseAI password reset code"
    message["From"] = settings.EMAIL_SENDER or "AdPulseAI"
    message["To"] = recipient
    message.set_content(
        f"Hello {name},\n\nYour AdPulseAI password reset code is: {code}\n\n"
        "This code expires in 10 minutes and can be used only once. If you did not request this, you can ignore this email.\n\n"
        "AdPulseAI Support"
    )
    message.add_alternative(
        f"""<!doctype html><html lang="en"><body style="font-family:Arial,sans-serif;color:#1d2327;line-height:1.6;">
        <h2 style="color:#2271b1;">AdPulseAI password reset</h2>
        <p>Hello {name},</p><p>Use this code to set a new password:</p>
        <p style="font-size:30px;font-weight:700;letter-spacing:8px;color:#2271b1;">{safe_code}</p>
        <p>This code expires in <strong>10 minutes</strong> and can be used only once.</p>
        <p>If you did not request this, you can ignore this email.</p></body></html>""",
        subtype="html",
    )
    try:
        await asyncio.to_thread(_send_sync, message)
        logger.info("Password reset email sent to user %s", recipient)
        return True
    except Exception:
        logger.exception("Could not send password reset email to user %s", recipient)
        return False
