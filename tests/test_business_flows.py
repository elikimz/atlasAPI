"""Regression coverage for critical authenticated commercial flows.

All tests run against an isolated in-memory SQLite database; no real payment
provider, user account, or production data is contacted.
"""

from collections.abc import AsyncIterator

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.database import get_async_db
from app.main import app
from app.models import models
from app.routers.auth import get_password_hash


TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
TEST_PASSWORD = "correct-horse-battery-staple"


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
        await connection.run_sync(models.Base.metadata.drop_all)
        await connection.run_sync(models.Base.metadata.create_all)

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
async def purchase_scenario(test_db: AsyncSession):
    starter_plan = models.Plan(
        name="Test Starter",
        price=50.0,
        daily_tasks_limit=2,
        validity_days=30,
        description="Plan used solely by the business-flow regression test.",
        is_active=True,
    )
    reward_task = models.VideoTask(
        plan=starter_plan,
        title="Test content review",
        description="An isolated task assigned after a successful purchase.",
        video_url="https://example.test/video",
        reward_amount=3.5,
    )
    referrer = models.User(
        username="flow_referrer",
        email="flow.referrer@example.test",
        phone_number="+254700000010",
        password_hash=get_password_hash(TEST_PASSWORD),
        role="user",
        is_admin=False,
        is_trained=True,
        withdrawal_wallet_balance=0.0,
        referral_code="FLOWREF",
    )
    purchaser = models.User(
        username="flow_purchaser",
        email="flow.purchaser@example.test",
        phone_number="+254700000011",
        password_hash=get_password_hash(TEST_PASSWORD),
        role="user",
        is_admin=False,
        is_trained=True,
        deposit_wallet_balance=100.0,
        withdrawal_wallet_balance=0.0,
        has_purchased_first_package=False,
    )
    test_db.add_all([starter_plan, reward_task, referrer, purchaser])
    await test_db.flush()
    test_db.add_all(
        [
            models.ReferralCode(user_id=referrer.id, code="FLOWREF"),
            models.ReferralRelationship(
                user_id=purchaser.id,
                referrer_id=referrer.id,
                referral_code_used="FLOWREF",
            ),
        ]
    )
    await test_db.commit()
    return {"plan": starter_plan, "task": reward_task, "referrer": referrer, "purchaser": purchaser}


async def login_header(client: AsyncClient, username: str) -> dict[str, str]:
    response = await client.post("/auth/login", json={"username": username, "password": TEST_PASSWORD})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.anyio
async def test_purchase_assigns_task_credits_referrer_and_rewards_completion(
    client: AsyncClient, test_db: AsyncSession, purchase_scenario: dict[str, models.User]
) -> None:
    plan = purchase_scenario["plan"]
    task = purchase_scenario["task"]
    purchaser = purchase_scenario["purchaser"]
    referrer = purchase_scenario["referrer"]
    headers = await login_header(client, purchaser.username)

    purchase = await client.post(f"/plans/purchase/{plan.id}", headers=headers)
    assert purchase.status_code == 200
    body = purchase.json()
    # Response shape is { plan_history: {...}, user: {...} }
    assert body["plan_history"]["plan_id"] == plan.id
    assert body["plan_history"]["status"] == "active"

    await test_db.refresh(purchaser)
    await test_db.refresh(referrer)
    assert purchaser.current_plan_id == plan.id
    assert purchaser.deposit_wallet_balance == pytest.approx(50.0)
    assert purchaser.has_purchased_first_package is True
    assert referrer.withdrawal_wallet_balance == pytest.approx(5.0)

    assigned_task = await test_db.scalar(
        select(models.UserVideoTask).where(
            models.UserVideoTask.user_id == purchaser.id,
            models.UserVideoTask.video_task_id == task.id,
        )
    )
    assert assigned_task is not None
    assert assigned_task.status == "pending"

    available = await client.get("/tasks/available", headers=headers)
    assert available.status_code == 200
    assert [entry["id"] for entry in available.json()] == [task.id]

    completed = await client.post("/tasks/complete", headers=headers, json={"video_task_id": task.id})
    assert completed.status_code == 200
    assert completed.json()["reward_amount"] == pytest.approx(3.5)

    await test_db.refresh(purchaser)
    await test_db.refresh(referrer)
    await test_db.refresh(assigned_task)
    assert purchaser.withdrawal_wallet_balance == pytest.approx(3.5)
    # $5 first-purchase invite commission plus the direct-tier $0.01 task rebate.
    assert referrer.withdrawal_wallet_balance == pytest.approx(5.01)
    assert assigned_task.status == "completed"

    replay = await client.post("/tasks/complete", headers=headers, json={"video_task_id": task.id})
    assert replay.status_code == 400
    assert "already completed" in replay.json()["detail"].lower()

    commission_logs = (await test_db.execute(
        select(models.EarningsLog).where(models.EarningsLog.user_id == referrer.id)
    )).scalars().all()
    assert {(entry.type, entry.amount) for entry in commission_logs} == {
        ("invite_commission", 5.0),
        ("task_rebate", 0.01),
    }
