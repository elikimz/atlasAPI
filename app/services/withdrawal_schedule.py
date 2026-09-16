from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_WITHDRAWAL_SCHEDULE = {
    "withdrawal_schedule_enabled": "true",
    "withdrawal_allowed_days": "mon,tue,wed,thu,fri,sat,sun",
    "withdrawal_start_time": "00:00",
    "withdrawal_end_time": "23:59",
    "withdrawal_timezone": "UTC",
}

_ALLOWED_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_SCHEDULE_KEYS = frozenset(DEFAULT_WITHDRAWAL_SCHEDULE)


def is_withdrawal_schedule_key(key: str) -> bool:
    return key in _SCHEDULE_KEYS


def _parse_time(value: str) -> tuple[int, int]:
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError("time must use HH:MM format")
    hour, minute = int(parts[0]), int(parts[1])
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("time must be between 00:00 and 23:59")
    return hour, minute


def _parse_days(value: str) -> list[str]:
    full_names = {
        "monday": "mon",
        "tuesday": "tue",
        "wednesday": "wed",
        "thursday": "thu",
        "friday": "fri",
        "saturday": "sat",
        "sunday": "sun",
    }
    days = [full_names.get(day.strip().lower(), day.strip().lower()) for day in value.split(",") if day.strip()]
    if not days:
        raise ValueError("select at least one allowed day")
    if any(day not in _ALLOWED_DAYS for day in days):
        raise ValueError("days must be comma-separated mon,tue,wed,thu,fri,sat,sun values")
    return list(dict.fromkeys(days))


def validate_schedule_value(key: str, value: str) -> str:
    value = value.strip()
    if key == "withdrawal_schedule_enabled":
        if value.lower() not in {"true", "false"}:
            raise ValueError("schedule enabled must be true or false")
        return value.lower()
    if key == "withdrawal_allowed_days":
        return ",".join(_parse_days(value))
    if key in {"withdrawal_start_time", "withdrawal_end_time"}:
        hour, minute = _parse_time(value)
        return f"{hour:02d}:{minute:02d}"
    if key == "withdrawal_timezone":
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone such as UTC or Africa/Nairobi") from exc
        return value
    raise ValueError("unsupported withdrawal schedule setting")


def normalize_schedule(values: dict[str, str]) -> dict[str, str]:
    schedule = DEFAULT_WITHDRAWAL_SCHEDULE.copy()
    for key in _SCHEDULE_KEYS:
        raw = values.get(key)
        if raw is None:
            continue
        try:
            schedule[key] = validate_schedule_value(key, str(raw))
        except ValueError:
            # A malformed legacy value must not lock users out. Keep the safe default.
            continue
    return schedule


def withdrawal_is_open(values: dict[str, str], now: datetime | None = None) -> bool:
    schedule = normalize_schedule(values)
    if schedule["withdrawal_schedule_enabled"] != "true":
        return True

    local_now = (now or datetime.now(ZoneInfo("UTC"))).astimezone(ZoneInfo(schedule["withdrawal_timezone"]))
    if local_now.strftime("%a").lower()[:3] not in _parse_days(schedule["withdrawal_allowed_days"]):
        return False

    start_hour, start_minute = _parse_time(schedule["withdrawal_start_time"])
    end_hour, end_minute = _parse_time(schedule["withdrawal_end_time"])
    current_minutes = local_now.hour * 60 + local_now.minute
    start_minutes = start_hour * 60 + start_minute
    end_minutes = end_hour * 60 + end_minute

    # Equal times intentionally mean all-day access, making 00:00/00:00 a safe option.
    if start_minutes == end_minutes:
        return True
    if start_minutes < end_minutes:
        return start_minutes <= current_minutes <= end_minutes
    # Overnight schedule, for example 22:00 through 06:00.
    return current_minutes >= start_minutes or current_minutes <= end_minutes


def schedule_summary(values: dict[str, str]) -> str:
    schedule = normalize_schedule(values)
    if schedule["withdrawal_schedule_enabled"] != "true":
        return "Withdrawals are currently open all the time."
    days = schedule["withdrawal_allowed_days"].replace(",", ", ")
    return f"Withdrawals are available {days}, {schedule['withdrawal_start_time']}–{schedule['withdrawal_end_time']} ({schedule['withdrawal_timezone']})."
