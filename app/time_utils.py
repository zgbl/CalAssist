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

MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
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
    month_match = re.search(
        r"\b("
        + "|".join(MONTHS.keys())
        + r")\s+(\d{1,2})(?:st|nd|rd|th)?(?:,\s*(\d{4}))?\b",
        lowered,
    )
    if month_match:
        month = MONTHS[month_match.group(1)]
        day = int(month_match.group(2))
        year = int(month_match.group(3) or local_now.year)
        return local_now.replace(year=year, month=month, day=day)
    if "today" in lowered or "later today" in lowered or "今天" in lowered:
        return local_now
    if "tomorrow" in lowered or "明天" in lowered:
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


def parse_reschedule_times(text: str, now: datetime, tz_name: str) -> tuple[datetime | None, datetime | None]:
    lowered = text.lower()
    if " to " not in lowered:
        return None, parse_start(text, now, tz_name)

    before, after = re.split(r"\s+to\s+", text, maxsplit=1, flags=re.IGNORECASE)
    old_start = parse_start(before, now, tz_name)
    new_start = parse_start(after, now, tz_name)
    if old_start is None:
        old_start = parse_start(text, now, tz_name)
    return old_start, new_start


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
