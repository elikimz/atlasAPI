from datetime import datetime, timedelta
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
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
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
    user_result = await db.execute(select(models.User).filter(models.User.email == otp_request.email))
    user = user_result.scalar_one_or_none()

    if not user:
        # New user: create with provided details
        user = models.User(
            email=otp_request.email,
            first_name=otp_request.first_name,
            last_name=otp_request.last_name,
            is_admin=(otp_request.email == "elijahkimani1293@gmail.com")
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
    expires_at = datetime.utcnow() + timedelta(minutes=10)

    otp_entry = models.OTP(
        email=otp_request.email,
        otp_code=otp_code,
        expires_at=expires_at
    )

    db.add(otp_entry)
    await db.commit()

    await send_email(
        otp_request.email,
        "Your OTP for Adpulse Capture",
        f"Your verification code is: {otp_code}"
    )

    return {"message": "OTP sent to email"}


@router.post("/auth/verify", response_model=Token)
async def verify_otp(otp_verify: OTPVerify, db: AsyncSession = Depends(get_async_db)):
    otp_entry = await db.execute(select(models.OTP).filter(
        models.OTP.email == otp_verify.email,
        models.OTP.otp_code == otp_verify.otp_code,
        models.OTP.expires_at > datetime.utcnow()
    ))
    otp_entry = otp_entry.scalar_one_or_none()

    if not otp_entry:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired OTP")

    user = await db.execute(select(models.User).filter(models.User.email == otp_verify.email))
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


@router.get("/wallet/balances", response_model=WalletBalances)
async def get_wallet_balances(current_user: models.User = Depends(get_current_user)):
    """Get the current user's deposit and withdrawal wallet balances."""
    return {
        "deposit_wallet_balance": current_user.deposit_wallet_balance or 0.0,
        "withdrawal_wallet_balance": current_user.withdrawal_wallet_balance or 0.0,
    }
