from datetime import datetime, timedelta
from typing import Optional
import os

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.database import get_async_db
from app.models import models

router = APIRouter()

# Configuration for JWT
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 1440))

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")

class UserCreate(BaseModel):
    email: str

class OTPRequest(BaseModel):
    email: str

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

    class Config:
        orm_mode = True

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
    user = await db.execute(select(models.User).filter(models.User.email == otp_request.email))
    user = user.scalar_one_or_none()
    if not user:
        # Create user if not exists
        user = models.User(email=otp_request.email)
        db.add(user)
        await db.commit()
        await db.refresh(user)

    # Generate OTP (for now, a simple fixed code for testing)
    otp_code = "123456" # In a real app, this would be a random code sent via email
    expires_at = datetime.utcnow() + timedelta(minutes=10) # OTP valid for 10 minutes

    otp_entry = models.OTP(email=otp_request.email, otp_code=otp_code, expires_at=expires_at)
    db.add(otp_entry)
    await db.commit()

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

from sqlalchemy import select

@router.get("/auth/me", response_model=UserInDB)
async def read_users_me(current_user: models.User = Depends(get_current_user)):
    return current_user
