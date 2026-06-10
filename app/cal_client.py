from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

import httpx

from app.config import Settings
from app.models import BookingSummary, CalApiError
from app.time_utils import to_utc_z


class CalGateway(Protocol):
    def list_bookings(self, status: str = "upcoming") -> list[BookingSummary]: ...
    def list_event_types(self) -> list[dict[str, Any]]: ...
    def create_booking(
        self,
        *,
        start: datetime,
        attendee_name: str,
        attendee_email: str,
        time_zone: str,
        length_in_minutes: int | None = None,
        title: str | None = None,
    ) -> BookingSummary: ...
    def cancel_booking(self, booking_uid: str, reason: str | None = None) -> BookingSummary: ...
    def reschedule_booking(self, booking_uid: str, start: datetime, reason: str | None = None) -> BookingSummary: ...


class CalClient:
    def __init__(self, settings: Settings, http: httpx.Client | None = None) -> None:
        self.settings = settings
        self.http = http or httpx.Client(timeout=20)

    def list_bookings(self, status: str = "upcoming") -> list[BookingSummary]:
        payload = self._request("GET", "/v2/bookings", version="2026-05-01", params={"status": status})
        return [booking_from_cal(item) for item in payload.get("data", [])]

    def list_event_types(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/v2/event-types", version="2024-06-14")
        return payload.get("data", [])

    def create_booking(
        self,
        *,
        start: datetime,
        attendee_name: str,
        attendee_email: str,
        time_zone: str,
        length_in_minutes: int | None = None,
        title: str | None = None,
    ) -> BookingSummary:
        body: dict[str, Any] = {
            "start": to_utc_z(start),
            "attendee": {
                "name": attendee_name,
                "email": attendee_email,
                "timeZone": time_zone,
                "language": "en",
            },
        }
        if self.settings.cal_event_type_id:
            body["eventTypeId"] = self.settings.cal_event_type_id
        else:
            body["eventTypeSlug"] = self.settings.cal_event_type_slug
            body["username"] = self.settings.cal_username
            if self.settings.cal_organization_slug:
                body["organizationSlug"] = self.settings.cal_organization_slug
        if length_in_minutes and self.settings.cal_send_length_in_minutes:
            body["lengthInMinutes"] = length_in_minutes
        if title:
            body["metadata"] = {"requestedTitle": title}

        payload = self._request("POST", "/v2/bookings", version="2026-02-25", json=body)
        return booking_from_cal(payload["data"])

    def cancel_booking(self, booking_uid: str, reason: str | None = None) -> BookingSummary:
        payload = self._request(
            "POST",
            f"/v2/bookings/{booking_uid}/cancel",
            version="2026-02-25",
            json={"cancellationReason": reason or "Cancelled from CalAssist"},
        )
        return booking_from_cal(payload["data"])

    def reschedule_booking(self, booking_uid: str, start: datetime, reason: str | None = None) -> BookingSummary:
        payload = self._request(
            "POST",
            f"/v2/bookings/{booking_uid}/reschedule",
            version="2026-02-25",
            json={"start": to_utc_z(start), "reschedulingReason": reason or "Rescheduled from CalAssist"},
        )
        return booking_from_cal(payload["data"])

    def _request(
        self,
        method: str,
        path: str,
        *,
        version: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.settings.cal_api_key:
            raise CalApiError("CAL_API_KEY is not configured")
        try:
            response = self.http.request(
                method,
                f"{self.settings.cal_api_base_url}{path}",
                params=params,
                json=json,
                headers={
                    "Authorization": f"Bearer {self.settings.cal_api_key}",
                    "cal-api-version": version,
                    "Content-Type": "application/json",
                },
            )
        except httpx.RequestError as exc:
            raise CalApiError(f"Could not reach Cal.com API: {exc}") from exc
        if response.status_code >= 400:
            try:
                payload: Any = response.json()
            except ValueError:
                payload = response.text
            raise CalApiError("Cal.com API request failed", status_code=response.status_code, payload=payload)
        return response.json()


def booking_from_cal(data: dict[str, Any]) -> BookingSummary:
    attendees = data.get("attendees") or []
    first_attendee = attendees[0] if attendees else {}
    return BookingSummary(
        uid=data.get("uid", ""),
        title=data.get("title"),
        status=data.get("status"),
        start=data.get("start"),
        end=data.get("end"),
        duration=data.get("duration"),
        attendee_name=first_attendee.get("name"),
        attendee_email=first_attendee.get("email"),
        raw=data,
    )


class MockCalClient:
    def __init__(self) -> None:
        self.bookings: dict[str, BookingSummary] = {}
        self.counter = 1

    def list_bookings(self, status: str = "upcoming") -> list[BookingSummary]:
        if status == "cancelled":
            return [item for item in self.bookings.values() if item.status == "cancelled"]
        return [item for item in self.bookings.values() if item.status != "cancelled"]

    def list_event_types(self) -> list[dict[str, Any]]:
        return [{"id": 123, "slug": "mock-30min", "title": "Mock 30 Minute Meeting", "length": 30}]

    def create_booking(
        self,
        *,
        start: datetime,
        attendee_name: str,
        attendee_email: str,
        time_zone: str,
        length_in_minutes: int | None = None,
        title: str | None = None,
    ) -> BookingSummary:
        uid = f"mock_{self.counter}"
        self.counter += 1
        booking = BookingSummary(
            uid=uid,
            title=title or "Cal.com booking",
            status="accepted",
            start=start,
            duration=length_in_minutes or 30,
            attendee_name=attendee_name,
            attendee_email=attendee_email,
            raw={"timeZone": time_zone},
        )
        self.bookings[uid] = booking
        return booking

    def cancel_booking(self, booking_uid: str, reason: str | None = None) -> BookingSummary:
        booking = self.bookings[booking_uid]
        self.bookings[booking_uid] = booking.model_copy(update={"status": "cancelled"})
        return self.bookings[booking_uid]

    def reschedule_booking(self, booking_uid: str, start: datetime, reason: str | None = None) -> BookingSummary:
        booking = self.bookings[booking_uid]
        self.bookings[booking_uid] = booking.model_copy(update={"start": start})
        return self.bookings[booking_uid]
