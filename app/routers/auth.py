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
    first_name: str
    last_name: str
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
        # New user: create with provided details
        user = models.User(
            email=email,
            first_name=otp_request.first_name,
            last_name=otp_request.last_name,
            is_admin=(email == "elijahkimani1293@gmail.com")
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

        # If a referral code was provided, look it up and record the signup
        if otp_request.referral_code:
            referral_result = await db.execute(
                select(models.ReferralCode).filter(models.ReferralCode.code == otp_request.referral_code)
            )
            referral = referral_result.scalar_one_or_none()
            if referral:
                referral.signups_count = (referral.signups_count or 0) + 1
                await db.commit()
    else:
        # Returning user: update name fields if they were previously empty
        if otp_request.first_name and not user.first_name:
            user.first_name = otp_request.first_name
        if otp_request.last_name and not user.last_name:
            user.last_name = otp_request.last_name
        if otp_request.email == "elijahkimani1293@gmail.com":
            user.is_admin = True
        await db.commit()

    otp_code = str(random.randint(100000, 999999))
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)

    # Ensure code is clean string
    otp_code = otp_code.strip()
    otp_entry = models.OTP(
        email=email,
        otp_code=otp_code,
        expires_at=expires_at
    )

    db.add(otp_entry)
    await db.commit()

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
    
    # DEBUG: Let's find ANY otp for this email to see what's in the DB
    debug_result = await db.execute(select(models.OTP).filter(models.OTP.email == email).order_by(models.OTP.created_at.desc()))
    all_user_otps = debug_result.scalars().all()
    
    # Check if OTP exists at all for this email and code
    otp_result = await db.execute(select(models.OTP).filter(
        models.OTP.email == email,
        models.OTP.otp_code == clean_otp
    ))
    otp_entry = otp_result.scalar_one_or_none()

    if not otp_entry:
        # If not found, provide more helpful debug info in the error (TEMPORARY)
        msg = f"Invalid code. Found {len(all_user_otps)} other codes for this email."
        if all_user_otps:
            msg += f" Latest code starts with {all_user_otps[0].otp_code[:2]}..."
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)
    
    # Check if the code is older than 15 minutes based on its creation time
    # This is more robust against server clock drift than checking an absolute expires_at
    current_time = datetime.now(timezone.utc)
    if otp_entry.created_at:
        # If created_at is naive, make it aware
        created_at = otp_entry.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        
        if (current_time - created_at) > timedelta(minutes=15):
            await db.delete(otp_entry)
            await db.commit()
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Verification code has expired")
    elif otp_entry.expires_at < current_time:
        # Fallback to expires_at if created_at is missing
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



@router.get("/auth/me", response_model=UserInDB)
async def read_users_me(current_user: models.User = Depends(get_current_user)):
    return current_user

@router.get("/auth/debug/otps")
async def debug_otps(db: AsyncSession = Depends(get_async_db)):
    result = await db.execute(select(models.OTP).order_by(models.OTP.created_at.desc()).limit(10))
    otps = result.scalars().all()
    return [{"email": o.email, "code": o.otp_code, "expires": o.expires_at, "created": o.created_at} for o in otps]


@router.get("/wallet/balances", response_model=WalletBalances)
async def get_wallet_balances(current_user: models.User = Depends(get_current_user)):
    """Get the current user's deposit and withdrawal wallet balances."""
    return {
        "deposit_wallet_balance": current_user.deposit_wallet_balance or 0.0,
        "withdrawal_wallet_balance": current_user.withdrawal_wallet_balance or 0.0,
    }
