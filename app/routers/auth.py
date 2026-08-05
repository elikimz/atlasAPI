from datetime import datetime, timedelta, timezone
from typing import Optional
import logging
import random
import string
import uuid

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database.database import get_async_db
from app.models import models

router = APIRouter()
logger = logging.getLogger(__name__)

SECRET_KEY = settings.SECRET_KEY
ALGORITHM = settings.JWT_ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES
REFRESH_TOKEN_EXPIRE_DAYS = settings.REFRESH_TOKEN_EXPIRE_DAYS
MAX_BCRYPT_PASSWORD_BYTES = 72

# The password flow is JSON-based, but OAuth2PasswordBearer still supplies the
# standard Authorization: Bearer parsing used by all protected application routes.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


class LoginRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    username: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1)

    @field_validator("password")
    @classmethod
    def password_must_fit_bcrypt(cls, value: str) -> str:
        if len(value.encode("utf-8")) > MAX_BCRYPT_PASSWORD_BYTES:
            raise ValueError("Password must be at most 72 UTF-8 bytes.")
        return value


class RegisterFinal(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8)
    phone_number: str = Field(min_length=3, max_length=32)
    referral_code: Optional[str] = Field(default=None, max_length=64)
    first_name: Optional[str] = Field(default=None, max_length=100)
    last_name: Optional[str] = Field(default=None, max_length=100)
    email: Optional[str] = Field(default=None, max_length=254)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        if not value.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Username may contain only letters, numbers, underscores, and hyphens.")
        return value

    @field_validator("password")
    @classmethod
    def password_must_fit_bcrypt(cls, value: str) -> str:
        if len(value.encode("utf-8")) > MAX_BCRYPT_PASSWORD_BYTES:
            raise ValueError("Password must be at most 72 UTF-8 bytes.")
        return value

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: Optional[str]) -> Optional[str]:
        if value is None or not value:
            return None
        return value.lower()


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    access_token_expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class LogoutResponse(BaseModel):
    message: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _is_expired(timestamp: datetime) -> bool:
    """Compare timestamps safely when a database driver returns naive UTC values."""
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp <= _utc_now()


def _ensure_password_is_supported(password: str) -> None:
    if len(password.encode("utf-8")) > MAX_BCRYPT_PASSWORD_BYTES:
        raise ValueError("Password must be at most 72 UTF-8 bytes.")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password without silently truncating it."""
    try:
        _ensure_password_is_supported(plain_password)
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def get_password_hash(password: str) -> str:
    """Hash a password after rejecting values bcrypt cannot safely represent."""
    _ensure_password_is_supported(password)
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expires_in = expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update(
        {
            "exp": _utc_now() + expires_in,
            "iat": _utc_now(),
            "jti": uuid.uuid4().hex,
            "token_type": "access",
        }
    )
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(data: dict, expires_delta: Optional[timedelta] = None) -> tuple[str, str, datetime]:
    token_id = uuid.uuid4().hex
    expiry = _utc_now() + (expires_delta or timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS))
    payload = data.copy()
    payload.update(
        {
            "exp": expiry,
            "iat": _utc_now(),
            "jti": token_id,
            "token_type": "refresh",
        }
    )
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM), token_id, expiry


async def _issue_token_pair(user: models.User, db: AsyncSession) -> Token:
    subject = str(user.id)
    token_data = {"sub": subject, "username": user.username, "role": user.role}
    access_token = create_access_token(token_data)
    refresh_token, token_id, expiry = create_refresh_token(token_data)
    db.add(models.RefreshToken(user_id=user.id, token_id=token_id, expires_at=expiry))
    await db.commit()
    return Token(
        access_token=access_token,
        refresh_token=refresh_token,
        access_token_expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


def _credentials_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Session expired. Please log in again.",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_async_db),
) -> models.User:
    credentials_exception = _credentials_exception()
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("token_type") != "access":
            raise credentials_exception
        subject = payload.get("sub")
        if not subject:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    if str(subject).isdigit():
        result = await db.execute(
            select(models.User)
            .options(selectinload(models.User.current_plan))
            .filter(models.User.id == int(subject))
        )
    else:
        # Tokens issued before this migration used usernames as subjects. They are
        # accepted only while still signed and still valid, then replaced at login.
        result = await db.execute(
            select(models.User)
            .options(selectinload(models.User.current_plan))
            .filter(models.User.username == str(subject))
        )
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exception
    if user.is_suspended:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been suspended. Please contact support.",
        )
    return user


async def get_current_admin_user(current_user: models.User = Depends(get_current_user)) -> models.User:
    if current_user.role != "admin" and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not enough permissions")
    return current_user


@router.post("/auth/login", response_model=Token)
async def login(login_request: LoginRequest, db: AsyncSession = Depends(get_async_db)) -> Token:
    login_identifier = login_request.username
    normalized_identifier = login_identifier.lower()
    result = await db.execute(
        select(models.User).filter(
            (func.lower(models.User.username) == normalized_identifier)
            | (func.lower(models.User.email) == normalized_identifier)
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
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Your account has been suspended.")
    return await _issue_token_pair(user, db)


@router.post("/auth/refresh", response_model=Token)
async def refresh_tokens(payload: RefreshRequest, db: AsyncSession = Depends(get_async_db)) -> Token:
    credentials_exception = _credentials_exception()
    try:
        decoded = jwt.decode(payload.refresh_token, SECRET_KEY, algorithms=[ALGORITHM])
        if decoded.get("token_type") != "refresh":
            raise credentials_exception
        token_id = decoded.get("jti")
        subject = decoded.get("sub")
        if not token_id or not subject or not str(subject).isdigit():
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    result = await db.execute(
        select(models.RefreshToken).filter(
            models.RefreshToken.token_id == token_id,
            models.RefreshToken.user_id == int(subject),
        )
    )
    refresh_session = result.scalar_one_or_none()
    if not refresh_session or refresh_session.revoked_at is not None or _is_expired(refresh_session.expires_at):
        raise credentials_exception

    user_result = await db.execute(select(models.User).filter(models.User.id == int(subject)))
    user = user_result.scalar_one_or_none()
    if not user or user.is_suspended:
        raise credentials_exception

    refresh_session.revoked_at = _utc_now()
    return await _issue_token_pair(user, db)


@router.post("/auth/logout", response_model=LogoutResponse)
async def logout(payload: RefreshRequest, db: AsyncSession = Depends(get_async_db)) -> LogoutResponse:
    try:
        decoded = jwt.decode(payload.refresh_token, SECRET_KEY, algorithms=[ALGORITHM])
        if decoded.get("token_type") != "refresh":
            raise _credentials_exception()
        token_id = decoded.get("jti")
        subject = decoded.get("sub")
        if not token_id or not subject or not str(subject).isdigit():
            raise _credentials_exception()
    except JWTError:
        # Logout should be idempotent for expired/invalid locally-held sessions.
        return LogoutResponse(message="Logged out successfully")

    result = await db.execute(
        select(models.RefreshToken).filter(
            models.RefreshToken.token_id == token_id,
            models.RefreshToken.user_id == int(subject),
        )
    )
    refresh_session = result.scalar_one_or_none()
    if refresh_session and refresh_session.revoked_at is None:
        refresh_session.revoked_at = _utc_now()
        await db.commit()
    return LogoutResponse(message="Logged out successfully")


@router.post("/auth/register/final", status_code=status.HTTP_201_CREATED)
async def register_final(data: RegisterFinal, db: AsyncSession = Depends(get_async_db)) -> dict:
    username = data.username
    try:
        existing_username = await db.execute(
            select(models.User).filter(func.lower(models.User.username) == username.lower())
        )
        if existing_username.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already registered")

        if data.email:
            existing_email = await db.execute(
                select(models.User).filter(func.lower(models.User.email) == data.email.lower())
            )
            if existing_email.scalar_one_or_none():
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

        if data.phone_number:
            existing_phone = await db.execute(
                select(models.User).filter(models.User.phone_number == data.phone_number)
            )
            if existing_phone.scalar_one_or_none():
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Phone number already registered")

        user = models.User(
            username=username,
            password_hash=get_password_hash(data.password),
            phone_number=data.phone_number,
            first_name=data.first_name,
            last_name=data.last_name,
            email=data.email,
            referral_code="".join(random.choices(string.ascii_uppercase + string.digits, k=8)),
        )
        db.add(user)
        await db.flush()

        if data.referral_code:
            referral_result = await db.execute(
                select(models.ReferralCode).filter(
                    func.lower(models.ReferralCode.code) == data.referral_code.strip().lower()
                )
            )
            referral = referral_result.scalar_one_or_none()
            if referral:
                db.add(
                    models.ReferralRelationship(
                        user_id=user.id,
                        referrer_id=referral.user_id,
                        referral_code_used=referral.code,
                    )
                )
                referral.signups_count = (referral.signups_count or 0) + 1
            else:
                logger.info("Registration completed without referral link: supplied code was not found")

        db.add(models.ReferralCode(user_id=user.id, code=user.referral_code))
        await db.commit()
        return {"message": "Registration successful"}
    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        logger.exception("Registration failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to complete registration. Please try again.",
        )


@router.get("/auth/me")
async def read_users_me(current_user: models.User = Depends(get_current_user)) -> dict:
    # Build current_plan object if available
    current_plan = None
    if current_user.current_plan_id:
        plan = current_user.current_plan
        if plan:
            current_plan = {
                "id": plan.id,
                "name": plan.name,
                "price": plan.price,
                "daily_tasks_limit": plan.daily_tasks_limit,
                "validity_days": plan.validity_days,
                "description": plan.description,
                "is_upgrade_only": plan.is_upgrade_only,
            }

    return {
        "id": current_user.id,
        "username": current_user.username,
        "first_name": current_user.first_name,
        "last_name": current_user.last_name,
        "email": current_user.email,
        "phone_number": current_user.phone_number,
        "role": current_user.role,
        "is_admin": current_user.is_admin,
        "is_trained": current_user.is_trained,
        "is_suspended": current_user.is_suspended,
        "referral_code": current_user.referral_code,
        "deposit_wallet_balance": current_user.deposit_wallet_balance or 0.0,
        "withdrawal_wallet_balance": current_user.withdrawal_wallet_balance or 0.0,
        "performance_bonus_balance": current_user.performance_bonus_balance or 0.0,
        "current_plan_id": current_user.current_plan_id,
        "plan_start_date": current_user.plan_start_date.isoformat() if current_user.plan_start_date else None,
        "plan_expiry_date": current_user.plan_expiry_date.isoformat() if current_user.plan_expiry_date else None,
        "current_plan": current_plan,
    }
