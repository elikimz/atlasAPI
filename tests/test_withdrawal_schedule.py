from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.services.withdrawal_schedule import (
    DEFAULT_WITHDRAWAL_SCHEDULE,
    normalize_schedule,
    validate_schedule_value,
    withdrawal_is_open,
)


def test_default_schedule_preserves_existing_all_day_behavior() -> None:
    assert withdrawal_is_open(DEFAULT_WITHDRAWAL_SCHEDULE, datetime(2026, 9, 16, 12, tzinfo=ZoneInfo("UTC")))


def test_schedule_can_close_weekends() -> None:
    schedule = normalize_schedule({
        **DEFAULT_WITHDRAWAL_SCHEDULE,
        "withdrawal_allowed_days": "mon,tue,wed,thu,fri",
        "withdrawal_start_time": "09:00",
        "withdrawal_end_time": "17:00",
        "withdrawal_timezone": "Africa/Nairobi",
    })
    saturday = datetime(2026, 9, 19, 12, tzinfo=ZoneInfo("Africa/Nairobi"))
    monday = datetime(2026, 9, 21, 12, tzinfo=ZoneInfo("Africa/Nairobi"))
    assert not withdrawal_is_open(schedule, saturday)
    assert withdrawal_is_open(schedule, monday)


def test_schedule_supports_overnight_window() -> None:
    schedule = normalize_schedule({
        **DEFAULT_WITHDRAWAL_SCHEDULE,
        "withdrawal_start_time": "22:00",
        "withdrawal_end_time": "06:00",
    })
    late = datetime(2026, 9, 16, 23, 0, tzinfo=ZoneInfo("UTC"))
    midday = datetime(2026, 9, 16, 12, 0, tzinfo=ZoneInfo("UTC"))
    assert withdrawal_is_open(schedule, late)
    assert not withdrawal_is_open(schedule, midday)


def test_invalid_admin_values_are_rejected() -> None:
    with pytest.raises(ValueError):
        validate_schedule_value("withdrawal_start_time", "25:00")
    with pytest.raises(ValueError):
        validate_schedule_value("withdrawal_allowed_days", "monday-ish")
    with pytest.raises(ValueError):
        validate_schedule_value("withdrawal_timezone", "Not/A_Timezone")
