from datetime import datetime, timedelta, timezone
from typing import Optional
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import random
import string
import asyncio
from sqlalchemy import select, delete, func, desc
from sqlalchemy.orm import selectinload
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone, timedelta

from app.database.database import get_async_db
from app.models import models
from app.config import settings

router = APIRouter()

# --- Configuration ---
SMTP_SERVER = settings.SMTP_SERVER
SMTP_PORT = settings.SMTP_PORT
EMAIL_SENDER = settings.EMAIL_SENDER
EMAIL_APP_PASSWORD = settings.EMAIL_APP_PASSWORD

SECRET_KEY = settings.SECRET_KEY
ALGORITHM = settings.JWT_ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")

# --- Schemas ---
class OTPRequest(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: str
    referral_code: Optional[str] = None

class OTPVerify(BaseModel):
    email: str
    otp_code: str

class Token(BaseModel):
    access_token: str
    token_type: str

# --- Helpers ---
async def send_email(to_email: str, subject: str, otp_code: str):
    """Asynchronous, non-blocking email sender with HTML template."""
    def _send():
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Your Verification Code - AdPulseAI"
        msg["From"] = f"AdPulseAI Support <{settings.EMAIL_SENDER}>"
        msg["To"] = to_email
        
        # Plain text fallback
        text = f"Your AdPulseAI verification code is: {otp_code}\n\nThis code will expire in 15 minutes."
        
        # Professional HTML template
        html = f"""
        <html>
        <body style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f9fafb; margin: 0; padding: 40px;">
            <div style="max-width: 500px; margin: 0 auto; background-color: #ffffff; border-radius: 16px; padding: 40px; box-shadow: 0 4px 12px rgba(0,0,0,0.05); border: 1px solid #e5e7eb;">
                <div style="text-align: center; margin-bottom: 30px;">
                    <h1 style="color: #5932EA; margin: 0; font-size: 28px; font-weight: 700;">AdPulseAI</h1>
                </div>
                <h2 style="color: #111827; font-size: 22px; font-weight: 700; text-align: center; margin-bottom: 10px;">Verify your email</h2>
                <p style="color: #4b5563; font-size: 16px; text-align: center; margin-bottom: 30px; line-height: 1.5;">
                    Please use the following 6-digit verification code to sign in to your account.
                </p>
                <div style="background-color: #f3f0ff; border-radius: 12px; padding: 20px; text-align: center; margin-bottom: 30px; border: 1px dashed #5932EA;">
                    <span style="font-size: 36px; font-weight: 800; color: #5932EA; letter-spacing: 10px; font-family: monospace;">{otp_code}</span>
                </div>
                <p style="color: #9ca3af; font-size: 14px; text-align: center; margin-bottom: 0;">
                    This code will expire in <b>15 minutes</b>.
                </p>
                <hr style="border: 0; border-top: 1px solid #f3f4f6; margin: 30px 0;">
                <p style="color: #9ca3af; font-size: 12px; text-align: center; line-height: 1.5;">
                    If you didn't request this code, you can safely ignore this email.<br>
                    &copy; 2024 AdPulseAI. All rights reserved.
                </p>
            </div>
        </body>
        </html>
        """
        
        msg.attach(MIMEText(text, "plain"))
        msg.attach(MIMEText(html, "html"))
        
        try:
            with smtplib.SMTP(settings.SMTP_SERVER, settings.SMTP_PORT) as server:
                server.starttls()
                server.login(settings.EMAIL_SENDER, settings.EMAIL_APP_PASSWORD)
                server.send_message(msg)
            return True
        except smtplib.SMTPAuthenticationError as e:
            print(f"ARCH-LOG [SMTP ERROR]: Authentication failed for {settings.EMAIL_SENDER}. SMTP response: {e.smtp_error} ({e.smtp_code}). This means the EMAIL_APP_PASSWORD is invalid or the Gmail account is blocking sign-in.")
            return False
        except smtplib.SMTPException as e:
            print(f"ARCH-LOG [SMTP ERROR]: SMTP error for {settings.EMAIL_SENDER}: {e}")
            return False
        except Exception as e:
            print(f"ARCH-LOG [SMTP ERROR]: Unexpected error: {type(e).__name__}: {e}")
            return False

    loop = asyncio.get_event_loop()
    success = await loop.run_in_executor(None, _send)
    if not success:
        print(f"ARCH-LOG [CRITICAL]: Failed to send email to {to_email}")
        # In production, we might want to still allow the flow to continue for debugging
        # or provide a very specific error if it's an SMTP issue.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="Email delivery failed. Please check your email address or try again later."
        )

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    # Use the configured expiration time (default to 1 year if not set)
    expire_minutes = ACCESS_TOKEN_EXPIRE_MINUTES or 525600
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=expire_minutes))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_async_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Session expired. Please log in again.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    result = await db.execute(
        select(models.User)
        .options(selectinload(models.User.current_plan))
        .filter(models.User.email == email)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exception
    
    if getattr(user, 'is_suspended', False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been suspended. Please contact support."
        )
        
    return user

# --- Auth Endpoints ---

@router.post("/auth/login")
async def login_otp(request: Request, otp_request: OTPRequest, db: AsyncSession = Depends(get_async_db)):
    """Robust Login/Registration Flow."""
    email = otp_request.email.strip().lower()
    print(f"ARCH-LOG [LOGIN ATTEMPT]: {email}")

    try:
        # 1. Handle User Existence
        user_result = await db.execute(select(models.User).filter(models.User.email == email))
        user = user_result.scalar_one_or_none()

        if user:
            # Existing User Check
            if getattr(user, 'is_suspended', False):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Your account has been suspended. Please contact support."
                )
            
            # Update names for returning users if missing
            if otp_request.first_name and not user.first_name:
                user.first_name = otp_request.first_name.strip()
            if otp_request.last_name and not user.last_name:
                user.last_name = otp_request.last_name.strip()
            
            # Ensure returning user has a referral code record
            if not user.referral_code:
                user.referral_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        else:
            # Registration Check
            if not otp_request.first_name or not otp_request.last_name:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="First and last name are required for registration."
                )
            
            # Create new user
            user = models.User(
                email=email,
                first_name=otp_request.first_name.strip(),
                last_name=otp_request.last_name.strip(),
                is_admin=False
            )
            
            # Generate referral code for new user
            random_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
            user.referral_code = random_code
            
            db.add(user)
            await db.flush() # Get user.id

            # Handle Referral Relationship
            if otp_request.referral_code:
                ref_result = await db.execute(select(models.ReferralCode).filter(models.ReferralCode.code == otp_request.referral_code.strip()))
                referral = ref_result.scalar_one_or_none()
                if referral:
                    relationship = models.ReferralRelationship(
                        user_id=user.id,
                        referrer_id=referral.user_id,
                        referral_code_used=otp_request.referral_code.strip()
                    )
                    db.add(relationship)
                    referral.signups_count = (referral.signups_count or 0) + 1
            
        # Ensure a record exists in the referral_codes table
        ref_code_result = await db.execute(select(models.ReferralCode).filter(models.ReferralCode.user_id == user.id))
        if not ref_code_result.scalar_one_or_none():
            db.add(models.ReferralCode(user_id=user.id, code=user.referral_code))

        # 2. Atomic OTP Generation
        # Invalidate old OTPs
        await db.execute(delete(models.OTP).filter(models.OTP.email == email))
        
        otp_code = str(random.randint(100000, 999999))
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
        
        otp_entry = models.OTP(
            email=email,
            otp_code=otp_code,
            expires_at=expires_at,
            ip_address=request.client.host if request.client else None
        )
        db.add(otp_entry)
        
        # Commit all changes (User creation/update + OTP generation)
        await db.commit()
        print(f"ARCH-LOG [OTP GENERATED]: {otp_code} for {email}")

        # 3. Send Email (Non-blocking)
        await send_email(email, "Your Verification Code - AdPulseAI", otp_code)

        return {"message": "Verification code sent to your email."}

    except HTTPException as he:
        await db.rollback()
        raise he
    except Exception as e:
        await db.rollback()
        error_msg = str(e)
        print(f"ARCH-LOG [LOGIN CRASH]: {error_msg}")
        
        # Check for specific database errors
        if "UniqueViolationError" in error_msg or "duplicate key" in error_msg:
            detail = "This email or referral code is already in use."
            status_code = status.HTTP_400_BAD_REQUEST
        else:
            detail = f"An unexpected error occurred: {error_msg}"
            status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            
        raise HTTPException(
            status_code=status_code,
            detail=detail
        )

@router.post("/auth/verify", response_model=Token)
async def verify_otp(otp_verify: OTPVerify, db: AsyncSession = Depends(get_async_db)):
    """State-based OTP Verification."""
    email = otp_verify.email.strip().lower()
    code = otp_verify.otp_code.strip()
    now = datetime.now(timezone.utc)

    # 1. Fetch latest unused OTP for this email
    result = await db.execute(
        select(models.OTP)
        .filter(func.lower(models.OTP.email) == email, models.OTP.is_used == False)
        .order_by(desc(models.OTP.created_at))
    )
    otp_entry = result.scalars().first()

    if not otp_entry:
        print(f"ARCH-LOG [VERIFY FAIL]: No active OTP for {email}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active verification code found. Please request a new one.")

    # 2. Validate Code
    if otp_entry.otp_code != code:
        print(f"ARCH-LOG [VERIFY FAIL]: Mismatch for {email}. Got {code}, expected {otp_entry.otp_code}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Incorrect verification code.")

    # 3. Validate Expiration
    # Ensure created_at/expires_at are UTC
    expires_at = otp_entry.expires_at.replace(tzinfo=timezone.utc) if otp_entry.expires_at.tzinfo is None else otp_entry.expires_at
    if now > expires_at:
        print(f"ARCH-LOG [VERIFY FAIL]: Expired for {email}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Verification code has expired. Please request a new one.")

    # 4. Mark as used and generate Token
    otp_entry.is_used = True
    
    user_result = await db.execute(select(models.User).filter(models.User.email == email))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User account error.")

    await db.commit()
    print(f"ARCH-LOG [VERIFY SUCCESS]: {email}")

    access_token = create_access_token(data={"sub": user.email, "role": user.role})
    return {"access_token": access_token, "token_type": "bearer"}

@router.get("/auth/me")
async def read_users_me(current_user: models.User = Depends(get_current_user)):
    plan_data = None
    if current_user.current_plan:
        plan_data = {
            "id": current_user.current_plan.id,
            "name": current_user.current_plan.name,
            "price": current_user.current_plan.price,
            "daily_tasks_limit": current_user.current_plan.daily_tasks_limit,
            "validity_days": current_user.current_plan.validity_days,
            "description": current_user.current_plan.description,
            "is_active": current_user.current_plan.is_active,
            "is_upgrade_only": current_user.current_plan.is_upgrade_only
        }
        
    return {
        "id": current_user.id,
        "first_name": current_user.first_name,
        "last_name": current_user.last_name,
        "email": current_user.email,
        "role": current_user.role,
        "is_admin": current_user.is_admin,
        "is_trained": current_user.is_trained,
        "deposit_wallet_balance": current_user.deposit_wallet_balance,
        "withdrawal_wallet_balance": current_user.withdrawal_wallet_balance,
        "performance_bonus_balance": current_user.performance_bonus_balance,
        "referral_code": current_user.referral_code,
        "current_plan_id": current_user.current_plan_id,
        "plan_start_date": current_user.plan_start_date,
        "plan_expiry_date": current_user.plan_expiry_date,
        "current_plan": plan_data
    }

@router.get("/wallet/balances")
async def get_wallet_balances(
    current_user: models.User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    """
    Return wallet balances.
    - deposit_wallet_balance: Funds recharged minus plan purchases/upgrades (never includes earnings).
    - withdrawal_wallet_balance: Cashable earnings (task rewards, rebates, commissions, released refunds).
    - performance_bonus_balance: Legacy field kept for backward compatibility.
    - pending_refund: Upgrade refund amount still within the 3-day lock period (not yet cashable).
    """
    from sqlalchemy import func as sqlfunc
    pending_res = await db.execute(
        select(sqlfunc.sum(models.UpgradeRefund.amount)).filter(
            models.UpgradeRefund.user_id == current_user.id,
            models.UpgradeRefund.status == "pending"
        )
    )
    pending_refund = pending_res.scalar() or 0.0

    return {
        "deposit_wallet_balance": current_user.deposit_wallet_balance,
        "withdrawal_wallet_balance": current_user.withdrawal_wallet_balance,
        "performance_bonus_balance": current_user.performance_bonus_balance,
        "pending_refund": pending_refund,
    }
