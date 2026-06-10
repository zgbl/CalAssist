from datetime import datetime, timezone

import httpx

from app.cal_client import CalClient, normalize_booking_emails
from app.config import Settings


def test_create_booking_sends_cal_v2_payload() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = request.headers
        captured["json"] = request.read().decode()
        return httpx.Response(
            201,
            json={
                "status": "success",
                "data": {
                    "uid": "abc123",
                    "title": "Intro",
                    "status": "accepted",
                    "start": "2026-06-11T18:00:00Z",
                    "duration": 30,
                    "attendees": [{"name": "Alex", "email": "alex@example.com"}],
                },
            },
        )

    client = CalClient(
        Settings(cal_api_key="cal_test", cal_event_type_id=123),
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    booking = client.create_booking(
        start=datetime(2026, 6, 11, 18, 0, tzinfo=timezone.utc),
        attendee_name="Alex",
        attendee_email="alex@example.com",
        time_zone="America/New_York",
        length_in_minutes=30,
        title="Intro",
        guest_emails=["rubio@whitehouse.com"],
    )
    assert booking.uid == "abc123"
    assert "https://api.cal.com/v2/bookings" == captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer cal_test"
    assert captured["headers"]["cal-api-version"] == "2026-02-25"
    assert '"eventTypeId":123' in captured["json"].replace(" ", "")
    assert '"guests":["rubio@whitehouse.com"]' in captured["json"].replace(" ", "")
    assert '"allowBookingOutOfBounds":true' in captured["json"].replace(" ", "")
    assert "allowConflicts" not in captured["json"]
    assert "lengthInMinutes" not in captured["json"]


def test_normalize_booking_emails_extracts_primary_and_guests() -> None:
    primary, guests = normalize_booking_emails(
        "dtrump@whitehouse.com and Rubio@whitehouse.com",
        [" extra@example.com "],
    )

    assert primary == "dtrump@whitehouse.com"
    assert guests == ["rubio@whitehouse.com", "extra@example.com"]
