from datetime import datetime, timedelta, timezone
from typing import Optional
import smtplib
from email.mime.text import MIMEText
import os
from sqlalchemy import select

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import get_async_db
from app.models import models
import random

router = APIRouter()

# Email configuration
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
EMAIL_SENDER = os.getenv("EMAIL_SENDER", "elijahkimani1293@gmail.com")
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD", "cgxmrmncbazlwyzy")

async def send_email(to_email: str, subject: str, body: str):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = EMAIL_SENDER
    msg["To"] = to_email

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_APP_PASSWORD)
            server.send_message(msg)
    except Exception as e:
        print(f"Failed to send email: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to send OTP email")

# Configuration for JWT
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 1440))

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")

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

class UserInDB(BaseModel):
    id: int
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    is_admin: bool = False
    deposit_wallet_balance: float = 0.0
    withdrawal_wallet_balance: float = 0.0

    class Config:
        from_attributes = True


class WalletBalances(BaseModel):
    deposit_wallet_balance: float
    withdrawal_wallet_balance: float

    class Config:
        from_attributes = True

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_async_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = await db.execute(select(models.User).filter(models.User.email == email))
    user = user.scalar_one_or_none()
    if user is None:
        raise credentials_exception
    return user

@router.post("/auth/login", response_model=dict)
async def login_otp(otp_request: OTPRequest, db: AsyncSession = Depends(get_async_db)):
    email = otp_request.email.strip().lower()
    user_result = await db.execute(select(models.User).filter(models.User.email == email))
    user = user_result.scalar_one_or_none()

    if not user:
        # Check if names are provided for new user registration
        if not otp_request.first_name or not otp_request.last_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Account not found. Please provide your first and last name to register."
            )
        # New user: create with provided details
        user = models.User(
            email=email,
            first_name=otp_request.first_name.strip(),
            last_name=otp_request.last_name.strip(),
            is_admin=(email == "elijahkimani1293@gmail.com")
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

        # Record referral if provided (wrapped in try/except for 100% safety)
        if otp_request.referral_code:
            try:
                referral_result = await db.execute(
                    select(models.ReferralCode).filter(models.ReferralCode.code == otp_request.referral_code.strip())
                )
                referral = referral_result.scalar_one_or_none()
                if referral:
                    relationship = models.ReferralRelationship(
                        user_id=user.id,
                        referrer_id=referral.user_id,
                        referral_code_used=otp_request.referral_code.strip()
                    )
                    db.add(relationship)
                    referral.signups_count = (referral.signups_count or 0) + 1
                    await db.commit()
            except Exception:
                await db.rollback()
    else:
        # Returning user: update name fields only if provided and user doesn't have them
        if otp_request.first_name and not user.first_name:
            user.first_name = otp_request.first_name.strip()
        if otp_request.last_name and not user.last_name:
            user.last_name = otp_request.last_name.strip()
        if otp_request.email == "elijahkimani1293@gmail.com":
            user.is_admin = True
        await db.commit()

    # --- Auto-generate Referral Code for user if they don't have one ---
    try:
        code_check = await db.execute(select(models.ReferralCode).filter(models.ReferralCode.user_id == user.id))
        if not code_check.scalar_one_or_none():
            import string
            # Generate a random 8-character code (uppercase letters and digits)
            random_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
            # Ensure it's unique
            while (await db.execute(select(models.ReferralCode).filter(models.ReferralCode.code == random_code))).scalar_one_or_none():
                random_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
                
            new_code = models.ReferralCode(
                user_id=user.id,
                code=random_code
            )
            db.add(new_code)
            await db.commit()
    except Exception as e:
        print(f"Safe Code Gen Error: {e}")
        await db.rollback()

    # Delete any existing OTPs for this email first to avoid confusion
    await db.execute(delete(models.OTP).filter(models.OTP.email == email))
    await db.commit()

    otp_code = str(random.randint(100000, 999999))
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)

    otp_entry = models.OTP(
        email=email,
        otp_code=otp_code,
        expires_at=expires_at
    )

    db.add(otp_entry)
    await db.commit()

    # ONLY send email if DB save succeeded
    await send_email(
        email,
        "Your OTP for Adpulse Capture",
        f"Your verification code is: {otp_code}"
    )

    return {"message": "OTP sent to email"}


@router.post("/auth/verify", response_model=Token)
async def verify_otp(otp_verify: OTPVerify, db: AsyncSession = Depends(get_async_db)):
    email = otp_verify.email.strip().lower()
    clean_otp = otp_verify.otp_code.strip()
    
    # Check if OTP exists for this email and code
    from sqlalchemy import desc
    otp_result = await db.execute(
        select(models.OTP)
        .filter(models.OTP.email == email, models.OTP.otp_code == clean_otp)
        .order_by(desc(models.OTP.id))
    )
    otp_entry = otp_result.scalars().first()

    if not otp_entry:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid verification code")
    
    # 60-minute safety window
    current_time = datetime.now(timezone.utc)
    created_at = otp_entry.created_at
    if created_at is None:
        created_at = datetime.now(timezone.utc)
    elif created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    
    if (current_time - created_at) > timedelta(minutes=60):
        await db.delete(otp_entry)
        await db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Verification code has expired")

    user = await db.execute(select(models.User).filter(models.User.email == email))
    user = user.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # OTP is valid, delete it and create access token
    await db.delete(otp_entry)
    await db.commit()

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.email},
        expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}



async def calculate_dynamic_withdrawal_balance(user_id: int, db: AsyncSession) -> float:
    """Calculate real withdrawal balance by summing all earned rewards and rebates."""
    # 1. Sum rewards from completed tasks
    task_result = await db.execute(
        select(func.sum(models.VideoTask.reward_amount))
        .join(models.UserVideoTask, models.VideoTask.id == models.UserVideoTask.video_task_id)
        .filter(models.UserVideoTask.user_id == user_id, models.UserVideoTask.status == "completed")
    )
    task_earnings = task_result.scalar() or 0.0
    
    # 2. Sum referral rebates (stored on ReferralCode for this user)
    referral_earnings = 0.0
    try:
        code_result = await db.execute(
            select(func.sum(models.ReferralCode.earned_amount + models.ReferralCode.task_rebate_amount))
            .filter(models.ReferralCode.user_id == user_id)
        )
        referral_earnings = code_result.scalar() or 0.0
    except Exception as e:
        print(f"Safe Balance Calc Error: {e}")
    
    return float(task_earnings + referral_earnings)

@router.get("/auth/me")
async def read_users_me(current_user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_async_db)):
    # Calculate real-time withdrawal balance from history
    withdrawal_balance = await calculate_dynamic_withdrawal_balance(current_user.id, db)
    
    return {
        "id": current_user.id,
        "email": current_user.email,
        "first_name": current_user.first_name,
        "last_name": current_user.last_name,
        "is_admin": getattr(current_user, "is_admin", False),
        "deposit_wallet_balance": getattr(current_user, "deposit_wallet_balance", 0.0) or 0.0,
        "withdrawal_wallet_balance": withdrawal_balance,
    }

@router.get("/wallet/balances")
async def get_wallet_balances(current_user: models.User = Depends(get_current_user), db: AsyncSession = Depends(get_async_db)):
    """Get the current user's deposit and withdrawal wallet balances."""
    # Calculate real-time withdrawal balance from history
    withdrawal_balance = await calculate_dynamic_withdrawal_balance(current_user.id, db)
    
    return {
        "deposit_wallet_balance": getattr(current_user, "deposit_wallet_balance", 0.0) or 0.0,
        "withdrawal_wallet_balance": withdrawal_balance,
    }
