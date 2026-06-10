from datetime import datetime
from zoneinfo import ZoneInfo

from app.time_utils import parse_start


def test_parse_start_supports_iso_date_with_ampm_time() -> None:
    start = parse_start(
        "Take this one: 2026-06-16 12:30 PM - 01:00 PM EDT",
        datetime(2026, 6, 10, 10, 0, tzinfo=ZoneInfo("America/New_York")),
        "America/New_York",
    )

    assert start == datetime(2026, 6, 16, 12, 30, tzinfo=ZoneInfo("America/New_York"))

