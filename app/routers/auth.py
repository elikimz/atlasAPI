from datetime import datetime, timedelta, timezone
from typing import Optional
import smtplib
from email.mime.text import MIMEText
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

from app.database.database import get_async_db
from app.models import models

router = APIRouter()

# --- Configuration ---
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
EMAIL_SENDER = os.getenv("EMAIL_SENDER", "elijahkimani1293@gmail.com")
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD", "cgxmrmncbazlwyzy")

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 1440))

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
async def send_email(to_email: str, subject: str, body: str):
    """Asynchronous, non-blocking email sender."""
    def _send():
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = EMAIL_SENDER
        msg["To"] = to_email
        try:
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls()
                server.login(EMAIL_SENDER, EMAIL_APP_PASSWORD)
                server.send_message(msg)
            return True
        except Exception as e:
            print(f"ARCH-LOG [SMTP ERROR]: {e}")
            return False

    loop = asyncio.get_event_loop()
    success = await loop.run_in_executor(None, _send)
    if not success:
        print(f"ARCH-LOG [CRITICAL]: Failed to send email to {to_email}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="Failed to send verification email. Please try again in a few minutes."
        )

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
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

        if not user:
            if not otp_request.first_name or not otp_request.last_name:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Account not found. Please provide your first and last name to register."
                )
            
            # Create new user
            user = models.User(
                email=email,
                first_name=otp_request.first_name.strip(),
                last_name=otp_request.last_name.strip(),
                is_admin=(email == "elijahkimani1293@gmail.com")
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
        else:
            # Update names for returning users if missing
            if otp_request.first_name and not user.first_name:
                user.first_name = otp_request.first_name.strip()
            if otp_request.last_name and not user.last_name:
                user.last_name = otp_request.last_name.strip()
            
            # Ensure returning user has a referral code record
            if not user.referral_code:
                user.referral_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
            
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
        await send_email(
            email,
            "Your Verification Code - Adpulse AI",
            f"Your verification code is: {otp_code}\n\nThis code will expire in 15 minutes."
        )

        return {"message": "Verification code sent to your email."}

    except HTTPException as he:
        await db.rollback()
        raise he
    except Exception as e:
        await db.rollback()
        print(f"ARCH-LOG [LOGIN CRASH]: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred. Our team has been notified."
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

    access_token = create_access_token(data={"sub": user.email})
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
        "email": current_user.email,
        "first_name": current_user.first_name,
        "last_name": current_user.last_name,
        "is_admin": current_user.is_admin,
        "deposit_wallet_balance": current_user.deposit_wallet_balance,
        "withdrawal_wallet_balance": current_user.withdrawal_wallet_balance,
        "referral_code": current_user.referral_code,
        "current_plan_id": current_user.current_plan_id,
        "plan_start_date": current_user.plan_start_date,
        "plan_expiry_date": current_user.plan_expiry_date,
        "current_plan": plan_data
    }

@router.get("/wallet/balances")
async def get_wallet_balances(current_user: models.User = Depends(get_current_user)):
    return {
        "deposit_wallet_balance": current_user.deposit_wallet_balance,
        "withdrawal_wallet_balance": current_user.withdrawal_wallet_balance,
    }
