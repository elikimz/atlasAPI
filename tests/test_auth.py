"""Regression coverage for the password-based authentication migration."""

from collections.abc import AsyncIterator
from datetime import timedelta

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.database.database import get_async_db
from app.main import app
from app.models.models import Base, PasswordResetCode, User
from app.routers import auth as auth_router
from app.routers.auth import _utc_now, get_password_hash
from app.services import email as email_service

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


@pytest.mark.anyio
async def test_password_reset_code_flow_changes_password_and_rejects_invalid_or_reused_code(
    client: AsyncClient,
    test_db: AsyncSession,
    registration_payload: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_codes: list[str] = []

    async def capture_reset_code(*, recipient: str | None, first_name: str | None, code: str) -> bool:
        assert recipient == registration_payload["email"]
        sent_codes.append(code)
        return True

    monkeypatch.setattr(auth_router, "send_password_reset_code_email", capture_reset_code)
    registration = await client.post("/auth/register/final", json=registration_payload)
    assert registration.status_code == 201

    request = await client.post(
        "/auth/password-reset/request", json={"email": registration_payload["email"]}
    )
    assert request.status_code == 200
    assert sent_codes and len(sent_codes[0]) == 6 and sent_codes[0].isdigit()
    reset_record = (await test_db.execute(select(PasswordResetCode))).scalars().one()
    assert reset_record.code_hash != sent_codes[0]
    assert not auth_router._is_expired(reset_record.expires_at)

    invalid_code = ("0" if sent_codes[0][0] != "0" else "1") + sent_codes[0][1:]
    invalid = await client.post(
        "/auth/password-reset/confirm",
        json={"email": registration_payload["email"], "code": invalid_code, "new_password": "new-secure-password"},
    )
    assert invalid.status_code == 400

    confirmed = await client.post(
        "/auth/password-reset/confirm",
        json={"email": registration_payload["email"], "code": sent_codes[0], "new_password": "new-secure-password"},
    )
    assert confirmed.status_code == 200
    reused = await client.post(
        "/auth/password-reset/confirm",
        json={"email": registration_payload["email"], "code": sent_codes[0], "new_password": "another-secure-password"},
    )
    assert reused.status_code == 400

    old_password_login = await client.post(
        "/auth/login", json={"username": registration_payload["email"], "password": registration_payload["password"]}
    )
    new_password_login = await client.post(
        "/auth/login", json={"username": registration_payload["email"], "password": "new-secure-password"}
    )
    assert old_password_login.status_code == 401
    assert new_password_login.status_code == 200


@pytest.mark.anyio
async def test_password_reset_reports_when_email_has_no_registered_account(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unexpected_delivery(**kwargs: object) -> bool:
        pytest.fail("mail must not be attempted for an unregistered address")

    monkeypatch.setattr(auth_router, "send_password_reset_code_email", unexpected_delivery)
    response = await client.post(
        "/auth/password-reset/request", json={"email": "unknown@example.invalid"}
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "No account is registered with that email address."


@pytest.mark.anyio
async def test_password_reset_rejects_expired_code(
    client: AsyncClient,
    test_db: AsyncSession,
    registration_payload: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent_codes: list[str] = []

    async def capture_reset_code(*, recipient: str | None, first_name: str | None, code: str) -> bool:
        sent_codes.append(code)
        return True

    monkeypatch.setattr(auth_router, "send_password_reset_code_email", capture_reset_code)
    await client.post("/auth/register/final", json=registration_payload)
    await client.post("/auth/password-reset/request", json={"email": registration_payload["email"]})
    record = (await test_db.execute(select(PasswordResetCode))).scalars().one()
    record.expires_at = _utc_now() - timedelta(seconds=1)
    await test_db.commit()

    expired = await client.post(
        "/auth/password-reset/confirm",
        json={"email": registration_payload["email"], "code": sent_codes[0], "new_password": "new-secure-password"},
    )
    assert expired.status_code == 400


@pytest.mark.anyio
async def test_failed_reset_email_delivery_invalidates_code(
    client: AsyncClient,
    test_db: AsyncSession,
    registration_payload: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_delivery(**kwargs: object) -> bool:
        return False

    monkeypatch.setattr(auth_router, "send_password_reset_code_email", fail_delivery)
    await client.post("/auth/register/final", json=registration_payload)
    response = await client.post(
        "/auth/password-reset/request", json={"email": registration_payload["email"]}
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "The reset email could not be delivered. Please try again later."
    record = (await test_db.execute(select(PasswordResetCode))).scalars().one()
    assert record.used_at is not None


@pytest.mark.anyio
async def test_reset_email_uses_requested_sender_name_and_address(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_messages = []

    def capture_message(message) -> None:
        captured_messages.append(message)

    monkeypatch.setattr(settings, "EMAIL_SENDER_NAME", "adpulseAI")
    monkeypatch.setattr(settings, "EMAIL_SENDER", "support@adpulseaiglobaltech.site")
    monkeypatch.setattr(email_service, "_send_sync", capture_message)

    delivered = await email_service.send_password_reset_code_email(
        recipient="test.user@example.com", first_name="Test", code="123456"
    )

    assert delivered is True
    assert str(captured_messages[0]["From"]) == "adpulseAI <support@adpulseaiglobaltech.site>"
    assert "123456" in captured_messages[0].get_body(preferencelist=("plain",)).get_content()
    html_part = next(part for part in captured_messages[0].walk() if part.get_content_type() == "text/html")
    html = html_part.get_content()
    assert "123456" in html
    assert "Tap and hold the code" in html
    assert "cid:" in html
    inline_logos = [
        part
        for part in captured_messages[0].walk()
        if part.get_content_maintype() == "image" and part.get_content_disposition() == "inline"
    ]
    assert len(inline_logos) == 1
    assert inline_logos[0].get_content_type() == "image/jpeg"


@pytest.mark.anyio
async def test_minimal_registration_generates_username_and_allows_phone_or_email_login(
    client: AsyncClient,
    test_db: AsyncSession,
) -> None:
    phone_number = "+254712345678"
    password = "safe-password-2026"
    registration = await client.post(
        "/auth/register/final",
        json={
            "phone_number": phone_number,
            "email": "orbit.member@example.com",
            "password": password,
            "referral_code": "",
        },
    )
    assert registration.status_code == 201
    created = await test_db.execute(select(User).where(User.phone_number == phone_number))
    user = created.scalar_one()
    assert user.username.startswith("member_")
    assert user.first_name is None
    assert user.last_name is None

    for identifier in (phone_number, "orbit.member@example.com"):
        login = await client.post("/auth/login", json={"username": identifier, "password": password})
        assert login.status_code == 200
        profile = await client.get(
            "/auth/me", headers={"Authorization": f"Bearer {login.json()['access_token']}"}
        )
        assert profile.status_code == 200
        assert profile.json()["username"] == user.username
