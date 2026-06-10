from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Intent(StrEnum):
    BOOK = "book"
    LIST = "list"
    CANCEL = "cancel"
    RESCHEDULE = "reschedule"
    CLARIFY = "clarify"


class BookingSummary(BaseModel):
    model_config = ConfigDict(extra="allow")

    uid: str
    title: str | None = None
    status: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    duration: int | None = None
    attendee_name: str | None = None
    attendee_email: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    conversation_id: str = "default"
    timezone: str | None = None
    now: datetime | None = None


class ChatResponse(BaseModel):
    status: Literal["ok", "needs_clarification", "error"]
    reply: str
    conversation_id: str
    action: str | None = None
    extractor: str | None = None
    bookings: list[BookingSummary] = Field(default_factory=list)
    booking: BookingSummary | None = None
    missing_fields: list[str] = Field(default_factory=list)


class AssistantAction(BaseModel):
    intent: Intent
    title: str | None = None
    start: datetime | None = None
    duration_minutes: int | None = 30
    attendee_name: str | None = None
    attendee_email: str | None = None
    guest_emails: list[str] = Field(default_factory=list)
    booking_uid: str | None = None
    lookup_start: datetime | None = None
    reason: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    missing_fields: list[str] = Field(default_factory=list)
    defaulted_duration: bool = False
    defaulted_title: bool = False

    @field_validator("start", "lookup_start", "date_from", "date_to")
    @classmethod
    def require_tz(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("datetime must be timezone-aware")
        return value


class PendingAction(BaseModel):
    action: AssistantAction


class CalApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, payload: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class LlmExtractionError(RuntimeError):
    pass
