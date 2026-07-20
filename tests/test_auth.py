"""Regression coverage for the password-based authentication migration."""

from collections.abc import AsyncIterator

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.database import get_async_db
from app.main import app
from app.models.models import Base, User
from app.routers.auth import get_password_hash

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
async def test_engine():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    yield engine
    await engine.dispose()


@pytest.fixture
async def test_db(test_engine) -> AsyncIterator[AsyncSession]:
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def client(test_db: AsyncSession) -> AsyncIterator[AsyncClient]:
    async def override_get_async_db() -> AsyncIterator[AsyncSession]:
        yield test_db

    app.dependency_overrides[get_async_db] = override_get_async_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as async_client:
        yield async_client
    app.dependency_overrides.clear()


@pytest.fixture
def registration_payload() -> dict[str, str]:
    return {
        "username": "test_user",
        "password": "correct-horse-battery-staple",
        "phone_number": "+254700000000",
        "first_name": "Test",
        "last_name": "User",
        "email": "test.user@example.com",
    }


@pytest.mark.anyio
async def test_health_and_protected_profile(client: AsyncClient) -> None:
    health = await client.get("/")
    assert health.status_code == 200
    assert "message" in health.json()

    profile = await client.get("/auth/me")
    assert profile.status_code == 401


@pytest.mark.anyio
async def test_password_registration_login_refresh_and_logout(
    client: AsyncClient, registration_payload: dict[str, str]
) -> None:
    registration = await client.post("/auth/register/final", json=registration_payload)
    assert registration.status_code == 201
    assert registration.json() == {"message": "Registration successful"}

    duplicate = await client.post(
        "/auth/register/final",
        json={**registration_payload, "username": "TEST_USER", "email": "other@example.com"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Username already registered"

    bad_login = await client.post(
        "/auth/login",
        json={"username": registration_payload["username"], "password": "incorrect-password"},
    )
    assert bad_login.status_code == 401

    login = await client.post(
        "/auth/login",
        json={"username": registration_payload["email"], "password": registration_payload["password"]},
    )
    assert login.status_code == 200
    first_tokens = login.json()
    assert first_tokens["token_type"] == "bearer"
    assert first_tokens["access_token"]
    assert first_tokens["refresh_token"]
    assert first_tokens["access_token_expires_in"] > 0

    profile = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {first_tokens['access_token']}"}
    )
    assert profile.status_code == 200
    assert profile.json()["username"] == registration_payload["username"]
    assert profile.json()["is_trained"] is False

    refreshed = await client.post("/auth/refresh", json={"refresh_token": first_tokens["refresh_token"]})
    assert refreshed.status_code == 200
    second_tokens = refreshed.json()
    assert second_tokens["refresh_token"] != first_tokens["refresh_token"]

    replayed_refresh = await client.post("/auth/refresh", json={"refresh_token": first_tokens["refresh_token"]})
    assert replayed_refresh.status_code == 401

    logout = await client.post("/auth/logout", json={"refresh_token": second_tokens["refresh_token"]})
    assert logout.status_code == 200
    assert logout.json() == {"message": "Logged out successfully"}

    revoked_refresh = await client.post("/auth/refresh", json={"refresh_token": second_tokens["refresh_token"]})
    assert revoked_refresh.status_code == 401


@pytest.mark.anyio
async def test_password_validation_and_suspended_account(
    client: AsyncClient, test_db: AsyncSession, registration_payload: dict[str, str]
) -> None:
    weak_password = await client.post(
        "/auth/register/final", json={**registration_payload, "password": "short"}
    )
    assert weak_password.status_code == 422

    suspended_user = User(
        username="suspended_user",
        password_hash=get_password_hash("correct-horse-battery-staple"),
        phone_number="+254700000001",
        role="user",
        is_suspended=True,
    )
    test_db.add(suspended_user)
    await test_db.commit()

    suspended_login = await client.post(
        "/auth/login",
        json={"username": "suspended_user", "password": "correct-horse-battery-staple"},
    )
    assert suspended_login.status_code == 403
    assert "suspended" in suspended_login.json()["detail"].lower()
