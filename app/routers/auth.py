from datetime import datetime, timedelta, timezone
from typing import Optional
import os
import random
import string
from sqlalchemy import select, func
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from passlib.context import CryptContext

from app.database.database import get_async_db
from app.models import models
from app.config import settings

router = APIRouter()

# --- Configuration ---
SECRET_KEY = settings.SECRET_KEY
ALGORITHM = settings.JWT_ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")

# --- Schemas ---
class LoginRequest(BaseModel):
    username: str
    password: str

class RegisterStep1(BaseModel):
    username: str
    password: str

class RegisterStep2(BaseModel):
    username: str
    phone_number: str
    referral_code: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None

class Token(BaseModel):
    access_token: str
    token_type: str

# --- Helpers ---
import bcrypt

def verify_password(plain_password, hashed_password):
    # Silently truncate password to 72 bytes for verification consistency
    # Bcrypt has a strict 72-byte limit. We use the raw bytes to ensure compliance.
    password_bytes = plain_password.encode("utf-8")[:72]
    if isinstance(hashed_password, str):
        hashed_password = hashed_password.encode("utf-8")
    return bcrypt.checkpw(password_bytes, hashed_password)

def get_password_hash(password):
    # Silently truncate password to 72 bytes before hashing
    # Bcrypt has a strict 72-byte limit. We use the raw bytes to ensure compliance.
    password_bytes = password.encode("utf-8")[:72]
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password_bytes, salt).decode("utf-8")

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
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
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    result = await db.execute(
        select(models.User)
        .filter(models.User.username == username)
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

@router.post("/auth/login", response_model=Token)
async def login(login_request: LoginRequest, db: AsyncSession = Depends(get_async_db)):
    # Support login by username or email (case-insensitive for email)
    username_lower = login_request.username.lower()
    result = await db.execute(
        select(models.User).filter(
            (models.User.username == login_request.username) |
            (func.lower(models.User.email) == username_lower)
        )
    )
    user = result.scalar_one_or_none()
    
    if not user or not verify_password(login_request.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if user.is_suspended:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been suspended."
        )

    access_token = create_access_token(data={"sub": user.username, "role": user.role})
    return {"access_token": access_token, "token_type": "bearer"}

# Final registration: all data sent together
class RegisterFinal(BaseModel):
    username: str
    password: str
    phone_number: str
    referral_code: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None

import logging
import traceback

logger = logging.getLogger(__name__)

@router.post("/auth/register/final")
async def register_final(data: RegisterFinal, db: AsyncSession = Depends(get_async_db)):
    try:
        logger.info(f"Starting registration for username: {data.username}")
        result = await db.execute(select(models.User).filter(models.User.username == data.username))
        if result.scalar_one_or_none():
            logger.warning(f"Registration failed: Username {data.username} already exists")
            raise HTTPException(status_code=400, detail="Username already registered")
        
        # Check for duplicate email if provided
        if data.email:
            email_result = await db.execute(select(models.User).filter(models.User.email == data.email))
            if email_result.scalar_one_or_none():
                raise HTTPException(status_code=400, detail="Email already registered")

        user = models.User(
            username=data.username,
            password_hash=get_password_hash(data.password),
            phone_number=data.phone_number,
            first_name=data.first_name,
            last_name=data.last_name,
            email=data.email,
            referral_code=''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        )
        
        db.add(user)
        await db.flush()
        logger.info(f"User {data.username} created with ID {user.id}")

        if data.referral_code:
            logger.info(f"Processing referral code: {data.referral_code}")
            ref_result = await db.execute(select(models.ReferralCode).filter(models.ReferralCode.code == data.referral_code.strip()))
            referral = ref_result.scalar_one_or_none()
            if referral:
                relationship = models.ReferralRelationship(
                    user_id=user.id,
                    referrer_id=referral.user_id,
                    referral_code_used=data.referral_code.strip()
                )
                db.add(relationship)
                referral.signups_count = (referral.signups_count or 0) + 1
                logger.info(f"Referral relationship created for user {user.id} and referrer {referral.user_id}")
            else:
                logger.warning(f"Referral code {data.referral_code} not found")
        
        # Ensure referral code record for the new user
        db.add(models.ReferralCode(user_id=user.id, code=user.referral_code))
        logger.info(f"Referral code {user.referral_code} created for new user {user.id}")
        
        await db.commit()
        logger.info(f"Registration successful for {data.username}")
        return {"message": "Registration successful"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error during registration for {data.username}: {str(e)}")
        logger.error(traceback.format_exc())
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")

@router.get("/auth/me")
async def read_users_me(current_user: models.User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "username": current_user.username,
        "first_name": current_user.first_name,
        "last_name": current_user.last_name,
        "email": current_user.email,
        "phone_number": current_user.phone_number,
        "role": current_user.role,
        "is_admin": current_user.is_admin,
        "referral_code": current_user.referral_code,
        "deposit_wallet_balance": current_user.deposit_wallet_balance or 0.0,
        "withdrawal_wallet_balance": current_user.withdrawal_wallet_balance or 0.0,
        "performance_bonus_balance": current_user.performance_bonus_balance or 0.0
    }
