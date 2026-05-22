import pytest
import httpx
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.database.database import get_async_db
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.models.models import Base
import os

# Use a separate test database (SQLite in-memory for speed and isolation)
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"

@pytest.fixture(scope="session")
async def test_engine():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()

@pytest.fixture
async def test_db(test_engine):
    async_session = sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session() as session:
        yield session
        await session.rollback()

@pytest.fixture
async def client(test_db):
    async def override_get_async_db():
        yield test_db
    
    app.dependency_overrides[get_async_db] = override_get_async_db
    async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()

@pytest.mark.anyio
async def test_root(client):
    response = await client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "Adpulse API is running 🚀"}

@pytest.mark.anyio
async def test_login_new_user_fail_without_names(client):
    # Should fail if names are missing for a new user
    response = await client.post("/auth/login", json={"email": "newuser@example.com"})
    assert response.status_code == 400
    assert "Please provide your first and last name to register" in response.json()["detail"]

@pytest.mark.anyio
async def test_login_registration_and_otp_flow(client):
    # 1. Register new user
    reg_response = await client.post("/auth/login", json={
        "email": "test@example.com",
        "first_name": "Test",
        "last_name": "User"
    })
    assert reg_response.status_code == 200
    assert reg_response.json()["message"] == "OTP sent to email"

@pytest.mark.anyio
async def test_auth_me_protected(client):
    # Should fail without token
    response = await client.get("/auth/me")
    assert response.status_code == 401
