from datetime import datetime
from zoneinfo import ZoneInfo

from app.agent import SchedulingAssistant
from app.cal_client import MockCalClient
from app.config import Settings
from app.llm import RuleBasedExtractor
from app.models import ChatRequest
from app.models import AssistantAction, Intent


TZ = "America/New_York"
NOW = datetime(2026, 6, 10, 10, 0, tzinfo=ZoneInfo(TZ))


def make_assistant() -> SchedulingAssistant:
    settings = Settings(
        cal_default_timezone=TZ,
        cal_default_attendee_email="founder@example.com",
        cal_event_type_id=123,
    )
    return SchedulingAssistant(MockCalClient(), RuleBasedExtractor(), settings)


def test_book_flow_asks_for_missing_email_then_books() -> None:
    assistant = make_assistant()
    first = assistant.handle(
        ChatRequest(
            conversation_id="c1",
            message="Book a 30-min intro with Alex tomorrow at 2pm",
            timezone=TZ,
            now=NOW,
        )
    )
    assert first.status == "needs_clarification"
    assert "attendee_email" in first.missing_fields

    second = assistant.handle(
        ChatRequest(
            conversation_id="c1",
            message="alex@example.com",
            timezone=TZ,
            now=NOW,
        )
    )
    assert second.status == "ok"
    assert second.extractor == "rule_based"
    assert second.booking is not None
    assert second.booking.attendee_email == "alex@example.com"


def test_list_bookings() -> None:
    assistant = make_assistant()
    assistant.handle(
        ChatRequest(
            conversation_id="c1",
            message="Book a 30-min intro with Alex alex@example.com tomorrow at 2pm",
            timezone=TZ,
            now=NOW,
        )
    )
    response = assistant.handle(ChatRequest(message="what's on my calendar tomorrow?", timezone=TZ, now=NOW))
    assert response.status == "ok"
    assert response.extractor == "rule_based"
    assert response.bookings
    assert "Upcoming bookings" in response.reply


def test_cancel_booking() -> None:
    assistant = make_assistant()
    booked = assistant.handle(
        ChatRequest(
            message="Book a 30-min intro with Alex alex@example.com tomorrow at 2pm",
            timezone=TZ,
            now=NOW,
        )
    )
    assert booked.booking is not None
    cancelled = assistant.handle(ChatRequest(message=f"cancel {booked.booking.uid}", timezone=TZ, now=NOW))
    assert cancelled.status == "ok"
    assert cancelled.booking is not None
    assert cancelled.booking.status == "cancelled"


def test_reschedule_booking() -> None:
    assistant = make_assistant()
    booked = assistant.handle(
        ChatRequest(
            message="Book a 30-min intro with Alex alex@example.com tomorrow at 2pm",
            timezone=TZ,
            now=NOW,
        )
    )
    assert booked.booking is not None
    response = assistant.handle(
        ChatRequest(message=f"move {booked.booking.uid} to tomorrow at 4pm", timezone=TZ, now=NOW)
    )
    assert response.status == "ok"
    assert response.booking is not None
    assert response.booking.start is not None
    assert response.booking.start.hour == 16


def test_clarify_intent_is_repaired_for_incomplete_booking() -> None:
    class ClarifyBookExtractor:
        name = "openrouter"
        histories: list[list[dict[str, str]]] = []

        def extract(
            self,
            message: str,
            now: datetime,
            timezone: str,
            history: list[dict[str, str]] | None = None,
        ) -> AssistantAction:
            self.histories.append(history or [])
            if "@" in message:
                return AssistantAction(intent=Intent.CLARIFY, missing_fields=["attendee_email"])
            return AssistantAction(
                intent=Intent.CLARIFY,
                title="Meeting",
                start=datetime(2026, 6, 11, 10, 0, tzinfo=ZoneInfo(TZ)),
                attendee_name="Tom Hanks",
                missing_fields=["attendee_email"],
            )

    settings = Settings(cal_default_timezone=TZ, cal_event_type_id=123)
    extractor = ClarifyBookExtractor()
    assistant = SchedulingAssistant(MockCalClient(), extractor, settings)
    first = assistant.handle(
        ChatRequest(
            conversation_id="repair",
            message="Book a meeting for me. Tomorrow 10AM EDT, with Tom Hanks.",
            timezone=TZ,
            now=NOW,
        )
    )
    assert first.status == "needs_clarification"
    assert first.action == "book"
    assert first.extractor == "openrouter"

    second = assistant.handle(
        ChatRequest(
            conversation_id="repair",
            message="HTom@livex.ai",
            timezone=TZ,
            now=NOW,
        )
    )
    assert second.status == "ok"
    assert second.action == "book"
    assert second.booking is not None
    assert second.booking.attendee_email == "HTom@livex.ai"
    assert extractor.histories[0] == []
    assert extractor.histories[1]
    assert any(
        item["role"] == "assistant" and "missing=attendee_email" in item["content"]
        for item in extractor.histories[1]
    )


def test_complete_book_request_is_repaired_from_message_fields() -> None:
    class VagueClarifyExtractor:
        name = "openrouter"

        def extract(
            self,
            message: str,
            now: datetime,
            timezone: str,
            history: list[dict[str, str]] | None = None,
        ) -> AssistantAction:
            return AssistantAction(intent=Intent.CLARIFY)

    assistant = SchedulingAssistant(
        MockCalClient(),
        VagueClarifyExtractor(),
        Settings(cal_default_timezone=TZ, cal_event_type_id=123),
    )
    response = assistant.handle(
        ChatRequest(
            message="Book a meeting for me. Tomorrow June 11, 2026, 10AM EDT, with Tom Hanks. HTom@livex.ai",
            timezone=TZ,
            now=NOW,
        )
    )
    assert response.status == "ok"
    assert response.action == "book"
    assert response.booking is not None
    assert response.booking.attendee_name == "Tom Hanks"
    assert response.booking.attendee_email == "HTom@livex.ai"
    assert response.booking.start is not None
    assert response.booking.start.hour == 10


def test_book_request_never_asks_for_booking_uid() -> None:
    class ConfusedUidExtractor:
        name = "openrouter"

        def extract(
            self,
            message: str,
            now: datetime,
            timezone: str,
            history: list[dict[str, str]] | None = None,
        ) -> AssistantAction:
            return AssistantAction(intent=Intent.RESCHEDULE, missing_fields=["booking_uid"])

    assistant = SchedulingAssistant(
        MockCalClient(),
        ConfusedUidExtractor(),
        Settings(cal_default_timezone=TZ, cal_event_type_id=123),
    )
    response = assistant.handle(
        ChatRequest(
            message="Book a meeting for me. Tomorrow June 11, 2026, 10AM EDT, with Tom Hanks. HTom@livex.ai",
            timezone=TZ,
            now=NOW,
        )
    )
    assert response.action == "book"
    assert "booking_uid" not in response.missing_fields
    assert "booking UID" not in response.reply


def test_booking_defaults_duration_and_subject_are_confirmed() -> None:
    class MinimalBookExtractor:
        name = "openrouter"

        def extract(
            self,
            message: str,
            now: datetime,
            timezone: str,
            history: list[dict[str, str]] | None = None,
        ) -> AssistantAction:
            return AssistantAction(
                intent=Intent.BOOK,
                start=datetime(2026, 6, 11, 10, 0, tzinfo=ZoneInfo(TZ)),
                attendee_name="Tom Hanks",
                attendee_email="HTom@livex.ai",
                title="Meeting",
                duration_minutes=None,
            )

    assistant = SchedulingAssistant(
        MockCalClient(),
        MinimalBookExtractor(),
        Settings(
            cal_default_timezone=TZ,
            cal_default_attendee_name="Xinyu Tu",
            cal_event_type_id=123,
        ),
    )
    response = assistant.handle(
        ChatRequest(
            message="Book a meeting for me. Tomorrow June 11, 2026, 10AM EDT, with Tom Hanks. HTom@livex.ai",
            timezone=TZ,
            now=NOW,
        )
    )
    assert response.status == "ok"
    assert response.booking is not None
    assert response.booking.title == "Meeting: Xinyu Tu and Tom Hanks"
    assert response.booking.duration == 30
    assert "defaulted duration to 30 minutes" in response.reply
    assert "defaulted subject to 'Meeting: Xinyu Tu and Tom Hanks'" in response.reply
