from datetime import datetime
from zoneinfo import ZoneInfo

from app.agent import SchedulingAssistant
from app.cal_client import MockCalClient
from app.config import Settings
from app.llm import RuleBasedExtractor
from app.models import BookingSummary, CalApiError, ChatRequest
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


def test_cancel_can_resolve_booking_by_day_and_attendee_name() -> None:
    assistant = make_assistant()
    booked = assistant.handle(
        ChatRequest(
            message="Book a 30-min intro with Alex alex@example.com tomorrow at 2pm",
            timezone=TZ,
            now=NOW,
        )
    )
    assert booked.booking is not None
    cancelled = assistant.handle(ChatRequest(message="取消我明天和 Alex 的会", timezone=TZ, now=NOW))
    assert cancelled.status == "ok"
    assert cancelled.action == "cancel"
    assert cancelled.booking is not None
    assert cancelled.booking.uid == booked.booking.uid
    assert cancelled.booking.status == "cancelled"


def test_cancel_by_details_says_not_found_when_no_booking_matches() -> None:
    assistant = make_assistant()
    response = assistant.handle(ChatRequest(message="取消我明天和 Alex 的会", timezone=TZ, now=NOW))
    assert response.status == "needs_clarification"
    assert response.action == "cancel"
    assert response.missing_fields == ["booking_not_found"]
    assert "could not find" in response.reply


def test_cancel_by_details_asks_uid_only_when_multiple_bookings_match() -> None:
    assistant = make_assistant()
    assistant.handle(
        ChatRequest(
            message="Book a 30-min intro with Alex alex@example.com tomorrow at 2pm",
            timezone=TZ,
            now=NOW,
        )
    )
    assistant.handle(
        ChatRequest(
            message="Book a 30-min intro with Alex alex@example.com tomorrow at 4pm",
            timezone=TZ,
            now=NOW,
        )
    )
    response = assistant.handle(ChatRequest(message="取消我明天和 Alex 的会", timezone=TZ, now=NOW))
    assert response.status == "needs_clarification"
    assert response.action == "cancel"
    assert response.missing_fields == ["booking_match_many"]
    assert "multiple matching" in response.reply


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


def test_reschedule_can_resolve_booking_by_original_time() -> None:
    assistant = make_assistant()
    booked = assistant.handle(
        ChatRequest(
            message="Book a 30-min intro with Alex alex@example.com tomorrow at 10am",
            timezone=TZ,
            now=NOW,
        )
    )
    assert booked.booking is not None
    response = assistant.handle(
        ChatRequest(
            message="reschedule my meeting tomorrow 10AM to June 12 3PM EDT",
            timezone=TZ,
            now=NOW,
        )
    )
    assert response.status == "ok"
    assert response.action == "reschedule"
    assert response.booking is not None
    assert response.booking.uid == booked.booking.uid
    assert response.booking.start is not None
    assert response.booking.start.day == 12
    assert response.booking.start.hour == 15


def test_reschedule_to_clause_overrides_llm_old_start_confusion() -> None:
    class OldStartExtractor:
        name = "openrouter"

        def extract(
            self,
            message: str,
            now: datetime,
            timezone: str,
            history: list[dict[str, str]] | None = None,
        ) -> AssistantAction:
            return AssistantAction(
                intent=Intent.RESCHEDULE,
                start=datetime(2026, 6, 11, 10, 0, tzinfo=ZoneInfo(TZ)),
            )

    cal = MockCalClient()
    assistant = SchedulingAssistant(
        cal,
        OldStartExtractor(),
        Settings(cal_default_timezone=TZ, cal_event_type_id=123),
    )
    booked = assistant.handle(
        ChatRequest(
            message="Book a 30-min intro with Alex alex@example.com tomorrow at 10am",
            timezone=TZ,
            now=NOW,
        )
    )
    assert booked.booking is not None
    response = assistant.handle(
        ChatRequest(
            message="reschedule my meeting tomorrow 10AM to June 12 3PM EDT",
            timezone=TZ,
            now=NOW,
        )
    )
    assert response.status == "ok"
    assert response.booking is not None
    assert response.booking.start is not None
    assert response.booking.start.day == 12
    assert response.booking.start.hour == 15


def test_reschedule_without_matching_time_gives_specific_guidance() -> None:
    assistant = make_assistant()
    response = assistant.handle(
        ChatRequest(
            message="reschedule my meeting tomorrow 10AM to June 12 3PM EDT",
            timezone=TZ,
            now=NOW,
        )
    )
    assert response.status == "needs_clarification"
    assert response.action == "reschedule"
    assert response.missing_fields == ["booking_match"]
    assert "could not find a unique upcoming booking" in response.reply


def test_reschedule_ignores_irrelevant_missing_fields_from_llm() -> None:
    class ConfusedRescheduleExtractor:
        name = "openrouter"

        def extract(
            self,
            message: str,
            now: datetime,
            timezone: str,
            history: list[dict[str, str]] | None = None,
        ) -> AssistantAction:
            return AssistantAction(intent=Intent.CLARIFY, missing_fields=["attendee_email", "unknown"])

    assistant = SchedulingAssistant(
        MockCalClient(),
        ConfusedRescheduleExtractor(),
        Settings(cal_default_timezone=TZ, cal_event_type_id=123),
    )
    response = assistant.handle(
        ChatRequest(
            message="reschedule my meeting tomorrow 10AM to June 12 3PM EDT",
            timezone=TZ,
            now=NOW,
        )
    )
    assert response.action == "reschedule"
    assert response.reply != "I need a little more information."
    assert "attendee_email" not in response.missing_fields


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
    assert second.booking.attendee_email == "htom@livex.ai"
    assert extractor.histories[0] == []
    assert extractor.histories[1]
    assert any(
        item["role"] == "assistant" and "missing=attendee_email" in item["content"]
        for item in extractor.histories[1]
    )


def test_book_repairs_invalid_multi_email_extraction() -> None:
    class BadEmailExtractor:
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
                title="Discuss how to stop iron war",
                start=datetime(2026, 6, 20, 11, 0, tzinfo=ZoneInfo(TZ)),
                attendee_name="Trump",
                attendee_email="dtrump@whitehouse.com and Rubio@whitehouse.com",
            )

    assistant = SchedulingAssistant(
        MockCalClient(),
        BadEmailExtractor(),
        Settings(cal_default_timezone=TZ, cal_event_type_id=123),
    )
    response = assistant.handle(
        ChatRequest(
            message=(
                "etup a new meeting for me Jun 20 11AM with Trump "
                "(dtrump@whitehouse.com) and Rubio( Rubio@whitehouse.com)"
            ),
            timezone=TZ,
            now=NOW,
        )
    )
    assert response.status == "ok"
    assert response.action == "book"
    assert response.booking is not None
    assert response.booking.attendee_email == "dtrump@whitehouse.com"
    assert response.booking.raw["guests"] == ["rubio@whitehouse.com"]


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
    assert response.booking.attendee_email == "htom@livex.ai"
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


def test_booking_conflict_reports_conflicting_booking_and_suggestions() -> None:
    class ConflictCalClient(MockCalClient):
        def create_booking(self, **kwargs):
            raise CalApiError(
                "Cal.com API request failed",
                payload={"error": {"message": "User either already has booking at this time or is not available"}},
            )

    cal = ConflictCalClient()
    cal.bookings["existing"] = BookingSummary(
        uid="existing",
        title="Meeting with Alex",
        status="accepted",
        start=datetime(2026, 6, 11, 14, 0, tzinfo=ZoneInfo(TZ)),
        end=datetime(2026, 6, 11, 14, 30, tzinfo=ZoneInfo(TZ)),
        attendee_name="Alex",
    )
    assistant = SchedulingAssistant(
        cal,
        RuleBasedExtractor(),
        Settings(cal_default_timezone=TZ, cal_event_type_id=123),
    )

    response = assistant.handle(
        ChatRequest(
            message="Book a 30-min intro with Bob bob@example.com tomorrow at 2pm",
            timezone=TZ,
            now=NOW,
        )
    )

    assert response.status == "needs_clarification"
    assert response.action == "book"
    assert response.missing_fields == ["start"]
    assert "conflicts with" in response.reply
    assert "Meeting with Alex" in response.reply
    assert "Alex" in response.reply
    assert "Suggested available times" in response.reply


def test_booking_conflict_followup_keeps_pending_attendees_and_title() -> None:
    class OneTimeConflictCalClient(MockCalClient):
        def __init__(self) -> None:
            super().__init__()
            self.fail_next_create = True

        def create_booking(self, **kwargs):
            if self.fail_next_create:
                self.fail_next_create = False
                raise CalApiError(
                    "Cal.com API request failed",
                    payload={"error": {"message": "User either already has booking at this time or is not available"}},
                )
            return super().create_booking(**kwargs)

    class ClarifyTimeExtractor:
        name = "openrouter"

        def extract(
            self,
            message: str,
            now: datetime,
            timezone: str,
            history: list[dict[str, str]] | None = None,
        ) -> AssistantAction:
            if "Take this one" in message:
                return AssistantAction(
                    intent=Intent.CLARIFY,
                    start=datetime(2026, 6, 16, 12, 30, tzinfo=ZoneInfo(TZ)),
                )
            return AssistantAction(
                intent=Intent.BOOK,
                title="Discuss the North Korea problem",
                start=datetime(2026, 6, 16, 11, 15, tzinfo=ZoneInfo(TZ)),
                attendee_name="Trump",
                attendee_email="dtrump@whitehouse.com",
                guest_emails=["rubio@whitehouse.com"],
            )

    cal = OneTimeConflictCalClient()
    cal.bookings["existing"] = BookingSummary(
        uid="existing",
        title="30 min meeting between Xinyu Tu and Trump",
        status="accepted",
        start=datetime(2026, 6, 16, 11, 0, tzinfo=ZoneInfo(TZ)),
        end=datetime(2026, 6, 16, 11, 30, tzinfo=ZoneInfo(TZ)),
        attendee_name="Trump",
    )
    assistant = SchedulingAssistant(
        cal,
        ClarifyTimeExtractor(),
        Settings(cal_default_timezone=TZ, cal_event_type_id=123),
    )

    first = assistant.handle(
        ChatRequest(
            conversation_id="conflict-followup",
            message=(
                "Schedule me a meeting with Trump dtrump@whitehouse.com and "
                "rubio@whitehouse.com For Jun 16 11:15AM To discuss the North Korea problem"
            ),
            timezone=TZ,
            now=NOW,
        )
    )
    assert first.status == "needs_clarification"
    assert "conflicts with" in first.reply

    second = assistant.handle(
        ChatRequest(
            conversation_id="conflict-followup",
            message="Take this one: 2026-06-16 12:30 PM - 01:00 PM EDT",
            timezone=TZ,
            now=NOW,
        )
    )

    assert second.status == "ok"
    assert second.action == "book"
    assert second.booking is not None
    assert second.booking.attendee_name == "Trump"
    assert second.booking.attendee_email == "dtrump@whitehouse.com"
    assert second.booking.raw["guests"] == ["rubio@whitehouse.com"]
    assert second.booking.start is not None
    assert second.booking.start.hour == 12
    assert second.booking.start.minute == 30


def test_non_availability_booking_error_stays_error() -> None:
    class EmailErrorCalClient(MockCalClient):
        def create_booking(self, **kwargs):
            raise CalApiError(
                "Cal.com API request failed",
                payload={"error": {"message": "responses - {email}email_validation_error"}},
            )

    assistant = SchedulingAssistant(
        EmailErrorCalClient(),
        RuleBasedExtractor(),
        Settings(cal_default_timezone=TZ, cal_event_type_id=123),
    )

    response = assistant.handle(
        ChatRequest(
            message="Book a 30-min intro with Bob bob@example.com tomorrow at 2pm",
            timezone=TZ,
            now=NOW,
        )
    )

    assert response.status == "error"
    assert "email_validation_error" in response.reply
