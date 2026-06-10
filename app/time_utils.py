from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def ensure_aware(value: datetime, tz_name: str) -> datetime:
    tz = ZoneInfo(tz_name)
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=tz)
    return value.astimezone(tz)


def to_utc_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def day_bounds(day: datetime, tz_name: str) -> tuple[datetime, datetime]:
    local = ensure_aware(day, tz_name)
    start = datetime.combine(local.date(), time.min, ZoneInfo(tz_name))
    return start, start + timedelta(days=1)


def parse_day(text: str, now: datetime, tz_name: str) -> datetime | None:
    lowered = text.lower()
    local_now = ensure_aware(now, tz_name)
    if "today" in lowered or "later today" in lowered:
        return local_now
    if "tomorrow" in lowered:
        return local_now + timedelta(days=1)
    for name, weekday in WEEKDAYS.items():
        if name in lowered:
            delta = (weekday - local_now.weekday()) % 7
            if delta == 0 or f"next {name}" in lowered:
                delta = 7
            return local_now + timedelta(days=delta)
    return None


def parse_time(text: str) -> tuple[int, int] | None:
    lowered = text.lower().replace("noon", "12pm").replace("midnight", "12am")
    if "afternoon" in lowered and not re.search(r"\d", lowered):
        return 14, 0
    if "morning" in lowered and not re.search(r"\d", lowered):
        return 9, 0
    matches = list(re.finditer(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", lowered))
    if not matches:
        return None
    match = next((candidate for candidate in matches if candidate.group(3)), matches[-1])
    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    meridiem = match.group(3)
    if meridiem == "pm" and hour != 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    if meridiem is None and 1 <= hour <= 7:
        hour += 12
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return None
    return hour, minute


def parse_start(text: str, now: datetime, tz_name: str) -> datetime | None:
    day = parse_day(text, now, tz_name)
    clock = parse_time(text)
    if day is None or clock is None:
        return None
    hour, minute = clock
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)


def parse_duration(text: str) -> int:
    lowered = text.lower()
    total = 0
    hour_match = re.search(r"\b(\d+)\s*(hour|hr|hours|hrs)\b", lowered)
    minute_match = re.search(r"\b(\d+)\s*(minute|min|minutes|mins)\b", lowered)
    compact_match = re.search(r"\b(\d+)[-\s]?min\b", lowered)
    if hour_match:
        total += int(hour_match.group(1)) * 60
    if minute_match:
        total += int(minute_match.group(1))
    elif compact_match:
        total += int(compact_match.group(1))
    return total or 30

