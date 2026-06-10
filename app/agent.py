from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.cal_client import CalGateway
from app.config import Settings
from app.llm import ActionExtractor, extract_email, extract_name, extract_title
from app.models import AssistantAction, CalApiError, ChatRequest, ChatResponse, Intent, LlmExtractionError, PendingAction
from app.time_utils import parse_duration, parse_start


class ChatMemory:
    def __init__(self) -> None:
        self.pending: dict[str, PendingAction] = {}
        self.history: dict[str, list[dict[str, str]]] = {}

    def get(self, conversation_id: str) -> PendingAction | None:
        return self.pending.get(conversation_id)

    def set(self, conversation_id: str, action: AssistantAction) -> None:
        self.pending[conversation_id] = PendingAction(action=action)

    def clear(self, conversation_id: str) -> None:
        self.pending.pop(conversation_id, None)

    def get_history(self, conversation_id: str) -> list[dict[str, str]]:
        return self.history.get(conversation_id, [])

    def add_history(self, conversation_id: str, role: str, content: str) -> None:
        items = self.history.setdefault(conversation_id, [])
        items.append({"role": role, "content": content})
        self.history[conversation_id] = items[-8:]


class SchedulingAssistant:
    def __init__(self, cal: CalGateway, extractor: ActionExtractor, settings: Settings, memory: ChatMemory | None = None) -> None:
        self.cal = cal
        self.extractor = extractor
        self.settings = settings
        self.memory = memory or ChatMemory()

    def handle(self, request: ChatRequest) -> ChatResponse:
        timezone = request.timezone or self.settings.cal_default_timezone
        now = request.now or datetime.now(ZoneInfo(timezone))
        extractor_name = getattr(self.extractor, "name", "unknown")
        history = self.memory.get_history(request.conversation_id)
        try:
            action = self.extractor.extract(request.message, now, timezone, history=history)
        except LlmExtractionError as exc:
            response = ChatResponse(
                status="error",
                reply=f"LLM extraction error: {exc}",
                conversation_id=request.conversation_id,
                extractor=extractor_name,
            )
            self._remember(request.conversation_id, request.message, response)
            return response
        action = repair_action_from_message(request.message, action, now, timezone)
        pending = self.memory.get(request.conversation_id)
        if pending:
            action = merge_pending(pending.action, action, request.message)

        missing = self._missing_fields(action)
        if missing:
            action.missing_fields = missing
            self.memory.set(request.conversation_id, action)
            response = ChatResponse(
                status="needs_clarification",
                reply=clarification_for(missing),
                conversation_id=request.conversation_id,
                action=action.intent.value,
                extractor=extractor_name,
                missing_fields=missing,
            )
            self._remember(request.conversation_id, request.message, response)
            return response

        try:
            response = self._execute(action, request.conversation_id, timezone, extractor_name)
            self.memory.clear(request.conversation_id)
            self._remember(request.conversation_id, request.message, response)
            return response
        except CalApiError as exc:
            response = ChatResponse(
                status="error",
                reply=f"Cal.com API error: {exc}. Details: {exc.payload}",
                conversation_id=request.conversation_id,
                action=action.intent.value,
                extractor=extractor_name,
            )
            self._remember(request.conversation_id, request.message, response)
            return response

    def _remember(self, conversation_id: str, user_message: str, response: ChatResponse) -> None:
        self.memory.add_history(conversation_id, "user", user_message)
        assistant_summary = response.reply
        if response.action:
            assistant_summary += f" [action={response.action}]"
        if response.missing_fields:
            assistant_summary += f" [missing={','.join(response.missing_fields)}]"
        if response.booking and response.booking.uid:
            assistant_summary += f" [booking_uid={response.booking.uid}]"
        self.memory.add_history(conversation_id, "assistant", assistant_summary)

    def _execute(self, action: AssistantAction, conversation_id: str, timezone: str, extractor_name: str) -> ChatResponse:
        if action.intent == Intent.LIST:
            bookings = self.cal.list_bookings(status="upcoming")
            return ChatResponse(
                status="ok",
                reply=format_booking_list(bookings),
                conversation_id=conversation_id,
                action=action.intent.value,
                extractor=extractor_name,
                bookings=bookings,
            )
        if action.intent == Intent.BOOK:
            assert action.start is not None
            assert action.attendee_name is not None
            assert action.attendee_email is not None
            action = apply_booking_defaults(action, self.settings)
            booking = self.cal.create_booking(
                start=action.start,
                attendee_name=action.attendee_name,
                attendee_email=str(action.attendee_email),
                time_zone=timezone,
                length_in_minutes=action.duration_minutes,
                title=action.title,
            )
            reply = f"Booked {booking.title or action.title or 'meeting'} for {booking.start}. Booking UID: {booking.uid}"
            confirmations = []
            if action.defaulted_duration:
                confirmations.append("defaulted duration to 30 minutes")
            if action.defaulted_title:
                confirmations.append(f"defaulted subject to '{action.title}'")
            if confirmations:
                reply += "\nConfirmed: " + "; ".join(confirmations) + "."
            return ChatResponse(
                status="ok",
                reply=reply,
                conversation_id=conversation_id,
                action=action.intent.value,
                extractor=extractor_name,
                booking=booking,
            )
        if action.intent == Intent.CANCEL:
            assert action.booking_uid is not None
            booking = self.cal.cancel_booking(action.booking_uid, action.reason)
            return ChatResponse(
                status="ok",
                reply=f"Cancelled booking {booking.uid}.",
                conversation_id=conversation_id,
                action=action.intent.value,
                extractor=extractor_name,
                booking=booking,
            )
        if action.intent == Intent.RESCHEDULE:
            assert action.booking_uid is not None
            assert action.start is not None
            booking = self.cal.reschedule_booking(action.booking_uid, action.start, action.reason)
            return ChatResponse(
                status="ok",
                reply=f"Rescheduled booking {booking.uid} to {booking.start}.",
                conversation_id=conversation_id,
                action=action.intent.value,
                extractor=extractor_name,
                booking=booking,
            )
        return ChatResponse(status="needs_clarification", reply="What would you like me to do?", conversation_id=conversation_id, extractor=extractor_name)

    def _missing_fields(self, action: AssistantAction) -> list[str]:
        missing = list(action.missing_fields)
        if action.intent == Intent.BOOK:
            if self.settings.has_cal_credentials and not self.settings.has_booking_target:
                missing.append("cal_event_type")
            if not action.start:
                missing.append("start")
            if not action.attendee_name:
                missing.append("attendee_name")
            if not action.attendee_email:
                missing.append("attendee_email")
        if action.intent == Intent.CANCEL and not action.booking_uid:
            missing.append("booking_uid")
        if action.intent == Intent.RESCHEDULE:
            if not action.booking_uid:
                missing.append("booking_uid")
            if not action.start:
                missing.append("start")
        return sorted(set(missing))


def merge_pending(pending: AssistantAction, new: AssistantAction, message: str) -> AssistantAction:
    data = pending.model_dump()
    if pending.intent == Intent.CLARIFY and new.intent != Intent.CLARIFY:
        data["intent"] = new.intent
    for key, value in new.model_dump().items():
        if key in {"intent", "missing_fields"}:
            continue
        if value not in (None, [], ""):
            data[key] = value
    if "@" in message and not data.get("attendee_email"):
        data["attendee_email"] = message.strip()
    if not data.get("attendee_name") and message.strip() and "@" not in message and len(message.split()) <= 4:
        data["attendee_name"] = message.strip()
    data["missing_fields"] = []
    return AssistantAction(**data)


def apply_booking_defaults(action: AssistantAction, settings: Settings) -> AssistantAction:
    updates = {}
    if not action.duration_minutes:
        updates["duration_minutes"] = 30
        updates["defaulted_duration"] = True
    if not action.title or action.title.strip().lower() == "meeting":
        host = settings.cal_default_attendee_name or "Host"
        attendee = action.attendee_name or "Guest"
        updates["title"] = f"Meeting: {host} and {attendee}"
        updates["defaulted_title"] = True
    return action.model_copy(update=updates) if updates else action


def repair_action_from_message(message: str, action: AssistantAction, now: datetime, timezone: str) -> AssistantAction:
    lowered = message.lower()
    repaired_intent: Intent | None = None

    # User intent verbs win over model confusion. Booking creates the UID; users
    # should never be asked for a booking UID when they are trying to create.
    if any(word in lowered for word in ["book", "schedule", "set up"]):
        repaired_intent = Intent.BOOK
    elif any(word in lowered for word in ["cancel", "delete", "remove"]):
        repaired_intent = Intent.CANCEL
    elif any(word in lowered for word in ["reschedule", "move", "push", "change"]):
        repaired_intent = Intent.RESCHEDULE
    elif action.intent != Intent.CLARIFY:
        return action
    elif any(word in lowered for word in ["meeting", "intro", "call"]) and "with" in lowered:
        repaired_intent = Intent.BOOK
    elif any(phrase in lowered for phrase in ["calendar", "what is on", "what's on", "show", "list"]):
        repaired_intent = Intent.LIST

    if repaired_intent is None:
        repaired = action
    else:
        updates = {"intent": repaired_intent}
        if repaired_intent == Intent.BOOK:
            updates["booking_uid"] = None
            if action.missing_fields:
                updates["missing_fields"] = [
                    field for field in action.missing_fields if field != "booking_uid"
                ]
        if repaired_intent == Intent.BOOK and not action.missing_fields:
            missing = []
            if not action.start:
                missing.append("start")
            if not action.attendee_name:
                missing.append("attendee_name")
            if not action.attendee_email:
                missing.append("attendee_email")
            updates["missing_fields"] = missing
        repaired = action.model_copy(update=updates)

    if repaired.intent == Intent.BOOK:
        return repair_booking_fields(message, repaired, now, timezone)
    return repaired


def repair_booking_fields(message: str, action: AssistantAction, now: datetime, timezone: str) -> AssistantAction:
    updates = {}
    if not action.start:
        parsed_start = parse_start(message, now, timezone)
        if parsed_start:
            updates["start"] = parsed_start
    if not action.duration_minutes:
        parsed_duration = parse_duration(message)
        updates["duration_minutes"] = parsed_duration
        if parsed_duration == 30:
            updates["defaulted_duration"] = True
    if not action.attendee_email:
        parsed_email = extract_email(message)
        if parsed_email:
            updates["attendee_email"] = parsed_email
    if not action.attendee_name:
        parsed_name = extract_name(message)
        if parsed_name:
            updates["attendee_name"] = parsed_name
    parsed_title = extract_title(message)
    if not action.title and parsed_title != "Meeting":
        updates["title"] = parsed_title

    repaired = action.model_copy(update=updates) if updates else action
    missing = [
        field
        for field, value in {
            "start": repaired.start,
            "attendee_name": repaired.attendee_name,
            "attendee_email": repaired.attendee_email,
        }.items()
        if not value
    ]
    return repaired.model_copy(update={"missing_fields": missing})


def clarification_for(missing: list[str]) -> str:
    if not missing:
        return "I could not identify the scheduling action. Please include the action, date/time, attendee name, and attendee email."
    if "cal_event_type" in missing:
        return "I need a Cal.com event type configured. Set CAL_EVENT_TYPE_ID or CAL_EVENT_TYPE_SLUG + CAL_USERNAME."
    if "start" in missing:
        return "What date and time should I use?"
    if "attendee_email" in missing:
        return "What is the attendee's email address?"
    if "attendee_name" in missing:
        return "What is the attendee's name?"
    if "booking_uid" in missing:
        return "Which booking UID should I use?"
    return "I need a little more information."


def format_booking_list(bookings: list) -> str:
    if not bookings:
        return "You have no upcoming bookings."
    lines = ["Upcoming bookings:"]
    for booking in bookings:
        lines.append(f"- {booking.uid}: {booking.title or 'Booking'} at {booking.start} ({booking.status})")
    return "\n".join(lines)
