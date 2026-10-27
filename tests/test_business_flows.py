"""Regression coverage for critical authenticated commercial flows.

All tests run against an isolated in-memory SQLite database; no real payment
provider, user account, or production data is contacted.
"""

from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
import statistics
from time import perf_counter

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.database import get_async_db
from app.main import app
from app.models import models
from app.routers.auth import get_password_hash, verify_password
from app.services.cache import cache


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
        membership_level=1,
        membership_commission_rate=0.10,
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
        current_plan=starter_plan,
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

    before_start = await client.post("/tasks/complete", headers=headers, json={"video_task_id": task.id})
    assert before_start.status_code == 409
    assert "start this task" in before_start.json()["detail"].lower()

    started = await client.post(f"/tasks/{task.id}/start", headers=headers)
    assert started.status_code == 200, started.text
    assert started.json()["required_session_seconds"] == 20
    started_again = await client.post(f"/tasks/{task.id}/start", headers=headers)
    assert started_again.status_code == 200
    assert started_again.json()["started_at"] == started.json()["started_at"]
    assigned_task = await test_db.scalar(
        select(models.UserVideoTask).where(
            models.UserVideoTask.user_id == purchaser.id,
            models.UserVideoTask.video_task_id == task.id,
        )
    )
    assigned_task.started_at = datetime.now(timezone.utc) - timedelta(seconds=21)
    await test_db.commit()

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

    await test_db.refresh(purchaser)
    await test_db.refresh(referrer)
    assert purchaser.withdrawal_wallet_balance == pytest.approx(3.5)
    assert referrer.withdrawal_wallet_balance == pytest.approx(5.01)
    commission_logs = (await test_db.execute(
        select(models.EarningsLog).where(models.EarningsLog.user_id == referrer.id)
    )).scalars().all()
    assert sorted((entry.type, entry.amount) for entry in commission_logs) == [
        ("invite_commission", 5.0),
        ("task_rebate", 0.01),
    ]
    task_logs = (await test_db.execute(
        select(models.EarningsLog).where(
            models.EarningsLog.user_id == purchaser.id,
            models.EarningsLog.type == "task_reward",
        )
    )).scalars().all()
    assert len(task_logs) == 1
    assert task_logs[0].amount == pytest.approx(3.5)


@pytest.mark.anyio
async def test_plan_purchase_upgrade_eligibility_and_full_price_wallet_rules(
    client: AsyncClient, test_db: AsyncSession
) -> None:
    from datetime import datetime, timedelta, timezone

    base = models.Plan(name="Base regression", price=20.0, validity_days=30, is_active=True)
    costly = models.Plan(name="Costly regression", price=60.0, validity_days=30, is_active=True)
    upgrade_only = models.Plan(
        name="Upgrade-only regression", price=80.0, validity_days=30,
        is_active=True, is_upgrade_only=True,
    )
    test_db.add_all([base, costly, upgrade_only])
    await test_db.flush()
    now = datetime.now(timezone.utc)
    expired = models.User(
        username="expired_regression", password_hash=get_password_hash(TEST_PASSWORD),
        current_plan_id=base.id, plan_expiry_date=now - timedelta(days=1),
        plan_purchase_price=20.0, deposit_wallet_balance=100.0,
    )
    active = models.User(
        username="active_regression", password_hash=get_password_hash(TEST_PASSWORD),
        current_plan_id=base.id, plan_expiry_date=now + timedelta(days=10),
        plan_purchase_price=20.0, deposit_wallet_balance=100.0,
        withdrawal_wallet_balance=2.0,
    )
    lower = models.Plan(name="Lower regression", price=10.0, validity_days=30, is_active=True)
    test_db.add_all([lower, expired, active])
    await test_db.flush()
    test_db.add_all([
        models.UserPlanHistory(user_id=active.id, plan_id=base.id, purchase_price=20.0,
                               expires_at=now + timedelta(days=10), status="active"),
    ])
    await test_db.commit()

    no_plan = models.User(
        username="new_regression", password_hash=get_password_hash(TEST_PASSWORD),
        deposit_wallet_balance=100.0,
    )
    test_db.add(no_plan)
    await test_db.commit()
    new_headers = await login_header(client, no_plan.username)
    rejected = await client.post(f"/plans/purchase/{upgrade_only.id}", headers=new_headers)
    assert rejected.status_code == 400
    assert "only as an upgrade" in rejected.json()["detail"]
    assert await test_db.scalar(select(func.count(models.UserPlanHistory.id)).where(
        models.UserPlanHistory.user_id == no_plan.id
    )) == 0

    expired_headers = await login_header(client, expired.username)
    expired_upgrade = await client.post(f"/plans/upgrade/{costly.id}", headers=expired_headers)
    assert expired_upgrade.status_code == 400
    assert "expired" in expired_upgrade.json()["detail"].lower()
    # Preserved expired-account rule: purchase must be strictly more expensive.
    same_or_lower = await client.post(f"/plans/purchase/{base.id}", headers=expired_headers)
    assert same_or_lower.status_code == 400
    assert "higher tier" in same_or_lower.json()["detail"]
    higher_purchase = await client.post(f"/plans/purchase/{costly.id}", headers=expired_headers)
    assert higher_purchase.status_code == 200, higher_purchase.text
    await test_db.refresh(expired)
    assert expired.current_plan_id == costly.id
    assert expired.deposit_wallet_balance == pytest.approx(40.0)

    active_headers = await login_header(client, active.username)
    lower_upgrade = await client.post(f"/plans/upgrade/{lower.id}", headers=active_headers)
    assert lower_upgrade.status_code == 400
    assert "higher tier" in lower_upgrade.json()["detail"]
    active_purchase = await client.post(f"/plans/purchase/{costly.id}", headers=active_headers)
    assert active_purchase.status_code == 400
    assert "already has an active plan" in active_purchase.json()["detail"]
    upgrade = await client.post(f"/plans/upgrade/{costly.id}", headers=active_headers)
    assert upgrade.status_code == 200, upgrade.text
    await test_db.refresh(active)
    assert active.current_plan_id == costly.id
    assert active.deposit_wallet_balance == pytest.approx(40.0)  # deduct full $60, not $40 net
    assert active.withdrawal_wallet_balance == pytest.approx(22.0)  # $20 old-plan refund


@pytest.mark.anyio
async def test_legacy_trained_flag_does_not_complete_other_certifications_and_requires_start(
    client: AsyncClient, test_db: AsyncSession
) -> None:
    user = models.User(
        username="legacy_training_regression", password_hash=get_password_hash(TEST_PASSWORD),
        is_trained=True,
    )
    first = models.Certification(name="Independent course A", is_active=True)
    second = models.Certification(name="Independent course B", is_active=True)
    test_db.add_all([user, first, second])
    await test_db.commit()
    headers = await login_header(client, user.username)

    listed = await client.get("/training/certifications", headers=headers)
    assert listed.status_code == 200
    assert {item["id"]: item["status"] for item in listed.json()} == {
        first.id: "available", second.id: "available",
    }
    complete_without_start = await client.post(
        f"/training/certifications/{first.id}/complete", headers=headers
    )
    assert complete_without_start.status_code == 404
    start = await client.post(f"/training/certifications/{first.id}/start", headers=headers)
    assert start.status_code == 200
    complete = await client.post(f"/training/certifications/{first.id}/complete", headers=headers)
    assert complete.status_code == 200

    refreshed = await client.get("/training/certifications", headers=headers)
    assert {item["id"]: item["status"] for item in refreshed.json()} == {
        first.id: "completed", second.id: "available",
    }


@pytest.mark.anyio
async def test_dashboard_cache_latency_benchmark(
    client: AsyncClient, purchase_scenario: dict[str, models.User]
) -> None:
    """Compare cold dashboard aggregation reads with warm cached reads in isolation."""
    headers = await login_header(client, purchase_scenario["purchaser"].username)

    cold_samples_ms: list[float] = []
    for _ in range(20):
        await cache.reset_for_tests()
        started = perf_counter()
        response = await client.get("/dashboard/summary", headers=headers)
        cold_samples_ms.append((perf_counter() - started) * 1000)
        assert response.status_code == 200

    await cache.reset_for_tests()
    priming_response = await client.get("/dashboard/summary", headers=headers)
    assert priming_response.status_code == 200
    expected_payload = priming_response.json()

    warm_samples_ms: list[float] = []
    for _ in range(100):
        started = perf_counter()
        response = await client.get("/dashboard/summary", headers=headers)
        warm_samples_ms.append((perf_counter() - started) * 1000)
        assert response.status_code == 200
        assert response.json() == expected_payload

    cold_median = statistics.median(cold_samples_ms)
    warm_median = statistics.median(warm_samples_ms)
    improvement_pct = ((cold_median - warm_median) / cold_median) * 100 if cold_median else 0.0

    print(
        "CACHE_BENCHMARK "
        f"endpoint=/dashboard/summary "
        f"cold_median_ms={cold_median:.3f} "
        f"warm_median_ms={warm_median:.3f} "
        f"improvement_pct={improvement_pct:.1f}"
    )

    # Verify cache behavior without imposing a hardware-specific absolute latency budget.
    assert warm_median < cold_median
    await cache.reset_for_tests()

@pytest.mark.anyio
async def test_invite_flow_registers_and_lists_three_referral_tiers(
    client: AsyncClient, test_db: AsyncSession
) -> None:
    async def register(username: str, email: str, phone: str, referral_code: str | None = None) -> None:
        payload = {
            "username": username,
            "password": TEST_PASSWORD,
            "phone_number": phone,
            "email": email,
            "first_name": username.title(),
            "last_name": "Invite",
        }
        if referral_code:
            payload["referral_code"] = referral_code
        response = await client.post("/auth/register/final", json=payload)
        assert response.status_code == 201, response.text

    await register("invite_root", "invite.root@example.test", "+254700000101")
    root_headers = await login_header(client, "invite_root")
    initial_summary = await client.get("/referrals/summary", headers=root_headers)
    assert initial_summary.status_code == 200
    assert initial_summary.json()["total_invites"] == 0
    root = await test_db.scalar(select(models.User).where(models.User.username == "invite_root"))
    root_code = await test_db.scalar(select(models.ReferralCode).where(models.ReferralCode.user_id == root.id))
    assert root_code is not None
    # The explicit pure-read mode must not insert a referral code.
    second_user = models.User(username="no_code_member", password_hash=get_password_hash(TEST_PASSWORD), referral_code=None)
    test_db.add(second_user)
    await test_db.commit()
    second_headers = await login_header(client, second_user.username)
    pure_read = await client.get("/referrals/codes?provision=false", headers=second_headers)
    assert pure_read.status_code == 200 and pure_read.json() == []
    assert await test_db.scalar(select(models.ReferralCode.id).where(models.ReferralCode.user_id == second_user.id)) is None
    first_provision = await client.post("/referrals/codes/provision", headers=second_headers)
    second_provision = await client.post("/referrals/codes/provision", headers=second_headers)
    assert first_provision.status_code == second_provision.status_code == 200
    assert first_provision.json() == second_provision.json()
    assert await test_db.scalar(select(func.count(models.ReferralCode.id)).where(models.ReferralCode.user_id == second_user.id)) == 1

    await register("invite_child", "invite.child@example.test", "+254700000102", root_code.code)
    child = await test_db.scalar(select(models.User).where(models.User.username == "invite_child"))
    child_code = await test_db.scalar(select(models.ReferralCode).where(models.ReferralCode.user_id == child.id))
    assert child_code is not None
    relationship = await test_db.scalar(select(models.ReferralRelationship).where(models.ReferralRelationship.user_id == child.id))
    assert relationship is not None and relationship.referrer_id == root.id

    await register("invite_grandchild", "invite.grandchild@example.test", "+254700000103", child_code.code)
    grandchild = await test_db.scalar(select(models.User).where(models.User.username == "invite_grandchild"))
    grandchild_code = await test_db.scalar(select(models.ReferralCode).where(models.ReferralCode.user_id == grandchild.id))
    assert grandchild_code is not None

    await register("invite_greatgrandchild", "invite.great@example.test", "+254700000104", grandchild_code.code)

    codes = await client.get("/referrals/codes", headers=root_headers)
    assert codes.status_code == 200
    assert codes.json()[0]["code"] == root_code.code

    active = await client.get("/referrals/active", headers=root_headers)
    assert active.status_code == 200
    assert {entry["tier"] for entry in active.json()} == {"A", "B", "C"}
    assert len(active.json()) == 3
    assert all(entry["status"] == "Invite Sent" and entry["is_active"] is False for entry in active.json())

    summary = await client.get("/referrals/summary", headers=root_headers)
    assert summary.status_code == 200, summary.text
    summary_data = summary.json()
    assert summary_data["users_referred"] == 1
    assert summary_data["total_invites"] == 1
    assert summary_data["active_invites"] == 0

@pytest.mark.anyio
async def test_crypto_minimum_is_three_but_other_deposits_remain_twenty(
    client: AsyncClient, purchase_scenario: dict[str, models.User]
) -> None:
    headers = await login_header(client, purchase_scenario["purchaser"].username)
    crypto = await client.post(
        "/payments/deposit",
        headers=headers,
        json={"amount": 3, "payment_method": "USDT", "network": "BEP20", "proof_url": "https://example.test/proof"},
    )
    assert crypto.status_code == 200, crypto.text

    below_crypto_minimum = await client.post(
        "/payments/deposit",
        headers=headers,
        json={"amount": 2.99, "payment_method": "USDT", "network": "BEP20", "proof_url": "https://example.test/proof"},
    )
    assert below_crypto_minimum.status_code == 422

    unsupported_network = await client.post(
        "/payments/deposit",
        headers=headers,
        json={"amount": 3, "payment_method": "USDT", "network": "ERC20", "proof_url": "https://example.test/proof"},
    )
    assert unsupported_network.status_code == 422

    below_other_minimum = await client.post(
        "/payments/deposit",
        headers=headers,
        json={"amount": 19.99, "payment_method": "Wise", "network": "Wise", "proof_url": "https://example.test/proof"},
    )
    assert below_other_minimum.status_code == 422

@pytest.mark.anyio
async def test_crypto_withdrawal_accounts_accept_only_bep20(
    client: AsyncClient, purchase_scenario: dict[str, models.User]
) -> None:
    headers = await login_header(client, purchase_scenario["purchaser"].username)
    valid = await client.post(
        "/withdrawal-accounts",
        headers=headers,
        json={"type": "crypto", "label": "Crypto Wallet", "address": "0x1234567890abcdef1234567890abcdef12345678", "network": "BEP20", "is_primary": True},
    )
    assert valid.status_code == 200, valid.text
    assert valid.json()["network"] == "BEP20"

    invalid = await client.post(
        "/withdrawal-accounts",
        headers=headers,
        json={"type": "crypto", "label": "Old Wallet", "address": "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd", "network": "ERC20", "is_primary": False},
    )
    assert invalid.status_code == 422

@pytest.mark.anyio
async def test_crypto_withdrawal_minimum_is_three_dollars(
    client: AsyncClient, test_db: AsyncSession, purchase_scenario: dict[str, models.User]
) -> None:
    purchaser = purchase_scenario["purchaser"]
    plan = purchase_scenario["plan"]
    purchaser.withdrawal_wallet_balance = 10.0
    await test_db.commit()
    headers = await login_header(client, purchaser.username)
    purchase = await client.post(f"/plans/purchase/{plan.id}", headers=headers)
    assert purchase.status_code == 200, purchase.text
    password = await client.post("/settings/withdrawal-password", headers=headers, json={"new_password": "1234"})
    assert password.status_code == 200, password.text
    account = await client.post(
        "/withdrawal-accounts",
        headers=headers,
        json={"type": "crypto", "label": "Crypto Wallet", "address": "0x1234567890abcdef1234567890abcdef12345678", "network": "BEP20", "is_primary": True},
    )
    assert account.status_code == 200, account.text
    withdrawal = await client.post(
        "/payments/withdraw",
        headers=headers,
        json={"amount": 2.99, "account_id": account.json()["id"], "password": "1234"},
    )
    assert withdrawal.status_code == 422
    assert "minimum crypto withdrawal" in withdrawal.json()["detail"].lower()

@pytest.mark.anyio
async def test_admin_can_reset_user_withdrawal_password(
    client: AsyncClient, test_db: AsyncSession, purchase_scenario: dict[str, models.User]
) -> None:
    admin = models.User(
        username="flow_admin",
        email="flow.admin@example.test",
        phone_number="+254700000099",
        password_hash=get_password_hash(TEST_PASSWORD),
        role="admin",
        is_admin=True,
    )
    test_db.add(admin)
    await test_db.commit()
    admin_headers = await login_header(client, admin.username)
    target = purchase_scenario["purchaser"]

    reset = await client.post(f"/admin/users/{target.id}/withdrawal-password", headers=admin_headers)
    assert reset.status_code == 200, reset.text
    await test_db.refresh(target)
    assert target.withdrawal_password is None

    target_headers = await login_header(client, target.username)
    set_again = await client.post(
        "/settings/withdrawal-password",
        headers=target_headers,
        json={"new_password": "new-withdrawal-password"},
    )
    assert set_again.status_code == 200, set_again.text
    await test_db.refresh(target)
    assert target.withdrawal_password is not None
    assert verify_password("new-withdrawal-password", target.withdrawal_password)


@pytest.mark.anyio
async def test_global_notification_read_and_dismiss_are_member_scoped(
    client: AsyncClient, test_db: AsyncSession
) -> None:
    first = models.User(username="notice_first", password_hash=get_password_hash(TEST_PASSWORD))
    second = models.User(username="notice_second", password_hash=get_password_hash(TEST_PASSWORD))
    global_notice = models.Notification(user_id=None, title="Test notice", message="Shared content", type="info")
    test_db.add_all([first, second, global_notice])
    await test_db.commit()
    first_headers = await login_header(client, first.username)
    second_headers = await login_header(client, second.username)

    first_initial = await client.get("/notifications", headers=first_headers)
    second_initial = await client.get("/notifications", headers=second_headers)
    assert first_initial.status_code == second_initial.status_code == 200
    assert first_initial.json()[0]["is_read"] is False
    assert second_initial.json()[0]["is_read"] is False

    marked = await client.post(
        "/notifications/mark-read",
        headers=first_headers,
        json={"notification_ids": [global_notice.id]},
    )
    assert marked.status_code == 200
    first_read = await client.get("/notifications", headers=first_headers)
    second_unread = await client.get("/notifications", headers=second_headers)
    assert first_read.json()[0]["is_read"] is True
    assert second_unread.json()[0]["is_read"] is False

    dismissed = await client.delete(f"/notifications/{global_notice.id}", headers=first_headers)
    assert dismissed.status_code == 200
    first_after_dismiss = await client.get("/notifications", headers=first_headers)
    second_after_dismiss = await client.get("/notifications", headers=second_headers)
    assert first_after_dismiss.json() == []
    assert second_after_dismiss.json()[0]["id"] == global_notice.id
    assert second_after_dismiss.json()[0]["is_read"] is False
    await test_db.refresh(global_notice)
    assert global_notice.user_id is None
    assert global_notice.is_read is False


@pytest.mark.anyio
async def test_payment_overview_counts_all_in_flight_states_without_fake_unpaid_zero(
    client: AsyncClient, test_db: AsyncSession
) -> None:
    user = models.User(username="payment_overview_regression", password_hash=get_password_hash(TEST_PASSWORD))
    test_db.add(user)
    await test_db.flush()
    test_db.add_all([
        models.Payment(user_id=user.id, amount=10.0, period="Oct 2026", type="deposit", status="pending"),
        models.Payment(user_id=user.id, amount=4.0, period="Oct 2026", type="payout", status="in_progress"),
        models.Payment(user_id=user.id, amount=20.0, period="Oct 2026", type="deposit", status="paid"),
        models.Payment(user_id=user.id, amount=3.0, period="Oct 2026", type="payout", status="paid"),
        models.Payment(user_id=user.id, amount=99.0, period="Oct 2026", type="deposit", status="rejected"),
    ])
    await test_db.commit()
    headers = await login_header(client, user.username)

    response = await client.get("/payments/overview", headers=headers)
    assert response.status_code == 200
    assert response.json() == {
        "total_paid": 23.0,
        "previous_unpaid": None,
        "current_pending": 14.0,
        "pending_deposits": 10.0,
        "pending_payouts": 4.0,
    }
