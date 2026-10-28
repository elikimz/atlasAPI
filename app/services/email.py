from __future__ import annotations

import asyncio
import logging
import smtplib
from pathlib import Path
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
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
    text_lines.extend(["", "Thank you,", "Pacecraft Support"])

    details_block = (
        f'<div style="margin:20px 0;padding:14px 16px;border-left:4px solid #{status_color};'
        f'background:#f6f7f7;color:#3c434a;font-size:14px;line-height:1.6;">'
        f'<strong>Admin note:</strong> {safe_details}</div>'
        if safe_details
        else ""
    )
    logo_cid = make_msgid(domain="pacecraft")
    logo_src = f"cid:{logo_cid[1:-1]}"
    html = f"""<!doctype html>
<html lang="en">
  <body style="margin:0;background:#F7F4EA;font-family:Arial,Helvetica,sans-serif;color:#192723;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F7F4EA;padding:32px 12px;">
      <tr><td align="center">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#fffefa;border:1px solid #d9e1d9;">
          <tr><td style="padding:20px 24px;background:#132f2a;color:#ffffff;">
            <img src="{logo_src}" width="44" height="44" alt="" style="vertical-align:middle;margin-right:12px;border:0;" /><span style="font-size:22px;font-weight:700;letter-spacing:.2px;vertical-align:middle;">Pacecraft</span>
            <div style="margin-top:4px;font-size:13px;opacity:.9;">{escape(title)}</div>
          </td></tr>
          <tr><td style="padding:28px;">
            <p style="margin:0 0 16px;font-size:16px;">Dear {safe_name},</p>
            <p style="margin:0 0 22px;font-size:15px;line-height:1.6;color:#50575e;">{safe_message}</p>
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #dcdcde;background:#f6f7f7;">
              <tr><td style="padding:14px 16px;font-size:13px;color:#646970;">Withdrawal amount</td><td align="right" style="padding:14px 16px;font-size:17px;font-weight:700;color:#1d2327;">KSh{amount:,.2f}</td></tr>
              <tr><td style="padding:14px 16px;border-top:1px solid #dcdcde;font-size:13px;color:#646970;">Status</td><td align="right" style="padding:14px 16px;border-top:1px solid #dcdcde;font-size:14px;font-weight:700;color:#{status_color};">{safe_status}</td></tr>
            </table>
            {details_block}
            <p style="margin:24px 0 0;font-size:13px;line-height:1.6;color:#697487;">If you did not request this, please contact Pacecraft Support.</p>
          </td></tr>
          <tr><td style="padding:18px 28px;border-top:1px solid #d5d3cb;font-size:12px;color:#697487;">© 2026 Pacecraft. All rights reserved.</td></tr>
        </table>
      </td></tr>
    </table>
  </body>
</html>"""

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((settings.EMAIL_SENDER_NAME, settings.EMAIL_SENDER)) if settings.EMAIL_SENDER else settings.EMAIL_SENDER_NAME
    message["To"] = recipient
    message.set_content("\n".join(text_lines))
    message.add_alternative(html, subtype="html")
    html_part = message.get_body(preferencelist=("html",))
    logo_path = Path(__file__).resolve().parent.parent / "assets" / "pacecraft-email-logo.png"
    if html_part is not None and logo_path.is_file():
        html_part.add_related(logo_path.read_bytes(), maintype="image", subtype="png", cid=logo_cid, disposition="inline", filename=logo_path.name)
    return message


def _send_sync(message: EmailMessage) -> None:
    if not settings.EMAIL_SENDER or not settings.EMAIL_APP_PASSWORD:
        raise RuntimeError("EMAIL_SENDER and EMAIL_APP_PASSWORD are required to send email")
    smtp_username = settings.SMTP_USERNAME or settings.EMAIL_SENDER

    if settings.SMTP_PORT == 465:
        with smtplib.SMTP_SSL(settings.SMTP_SERVER, settings.SMTP_PORT, timeout=20) as smtp:
            smtp.login(smtp_username, settings.EMAIL_APP_PASSWORD)
            smtp.send_message(message)
        return

    with smtplib.SMTP(settings.SMTP_SERVER, settings.SMTP_PORT, timeout=20) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(smtp_username, settings.EMAIL_APP_PASSWORD)
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
        f"Amount: KSh{amount:,.2f}. Status: APPROVED."
        if approved
        else f"Your withdrawal was canceled and the amount of KSh{amount:,.2f} has been returned to your withdrawal wallet. Status: CANCELED."
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
            f"KSh{amount:,.2f} withdrawal request. Your request has been recorded in our "
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
    message["Subject"] = "Your Pacecraft password reset code"
    message["From"] = formataddr((settings.EMAIL_SENDER_NAME, settings.EMAIL_SENDER)) if settings.EMAIL_SENDER else settings.EMAIL_SENDER_NAME
    message["To"] = recipient
    message.set_content(
        f"PACECRAFT | PASSWORD RESET\n\nHello {first_name or 'there'},\n\n"
        f"Your six-digit reset code is:\n\n{code}\n\n"
        "Select and copy the code above. It expires in 10 minutes and can be used only once.\n\n"
        "If you did not request this, you can ignore this email.\n\nPacecraft Support"
    )
    logo_cid = make_msgid(domain="pacecraft")
    logo_src = f"cid:{logo_cid[1:-1]}"
    html = f"""<!doctype html>
<html lang="en">
  <body style="margin:0;padding:0;background-color:#F7F4EA;color:#192723;font-family:Arial,Helvetica,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#F7F4EA;padding:28px 12px;">
      <tr><td align="center">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background-color:#fffefa;border:1px solid #d9e1d9;border-radius:18px;overflow:hidden;">
          <tr><td align="center" style="padding:28px 24px 24px;background-color:#132f2a;">
            <img src="{logo_src}" width="88" height="88" alt="Pacecraft mark" style="display:block;width:88px;height:88px;border:0;border-radius:14px;" />
            <p style="margin:14px 0 0;color:#ffffff;font-size:22px;font-weight:700;letter-spacing:.3px;">Pacecraft</p>
            <p style="margin:5px 0 0;color:#c5df55;font-size:11px;font-weight:700;letter-spacing:2px;">ACCOUNT SECURITY</p>
          </td></tr>
          <tr><td style="padding:30px 28px 20px;">
            <h1 style="margin:0 0 12px;color:#192723;font-size:24px;line-height:1.25;">Reset your password</h1>
            <p style="margin:0 0 12px;color:#34445c;font-size:15px;line-height:1.6;">Hello {name},</p>
            <p style="margin:0 0 22px;color:#435064;font-size:14px;line-height:1.65;">Use this one-time code to securely reset your Pacecraft password.</p>
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f5ef;border:1px solid #d9e1d9;border-radius:14px;">
              <tr><td align="center" style="padding:22px 14px 18px;">
                <p style="margin:0 0 10px;color:#54615a;font-size:11px;font-weight:700;letter-spacing:1.6px;">YOUR 6-DIGIT CODE</p>
              <p style="margin:0;color:#174d43;font-family:'Courier New',Courier,monospace;font-size:36px;font-weight:700;letter-spacing:7px;line-height:1.3;">{safe_code}</p>
                <p style="margin:12px 0 0;color:#54615a;font-size:12px;line-height:1.5;">Tap and hold the code, then choose <strong>Copy</strong>.</p>
              </td></tr>
            </table>
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:18px;">
              <tr><td style="padding:12px 14px;background-color:#f3f5e9;border-left:3px solid #c5df55;border-radius:4px;color:#54615a;font-size:13px;line-height:1.55;">
                This code expires in <strong>10 minutes</strong> and can be used only once.
              </td></tr>
            </table>
            <p style="margin:20px 0 0;color:#718096;font-size:12px;line-height:1.6;">If you did not request a password reset, you can safely ignore this email. Your password will not change unless the code is used.</p>
          </td></tr>
          <tr><td align="center" style="padding:18px 24px;background-color:#f4f5ef;border-top:1px solid #d9e1d9;color:#718078;font-size:11px;line-height:1.6;">
            Pacecraft Support<br />Automated account security message
          </td></tr>
        </table>
      </td></tr>
    </table>
  </body>
</html>"""
    message.add_alternative(html, subtype="html")
    html_part = message.get_body(preferencelist=("html",))
    logo_path = Path(__file__).resolve().parent.parent / "assets" / "pacecraft-email-logo.png"
    if html_part is not None and logo_path.is_file():
        html_part.add_related(
            logo_path.read_bytes(),
            maintype="image",
            subtype="png",
            cid=logo_cid,
            disposition="inline",
            filename=logo_path.name,
        )
    try:
        await asyncio.to_thread(_send_sync, message)
        logger.info("Password reset email sent to user %s", recipient)
        return True
    except Exception:
        logger.exception("Could not send password reset email to user %s", recipient)
        return False
