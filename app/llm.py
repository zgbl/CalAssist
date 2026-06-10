from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Protocol

import httpx

from app.config import Settings
from app.models import AssistantAction, Intent, LlmExtractionError
from app.time_utils import day_bounds, ensure_aware, parse_duration, parse_reschedule_times, parse_start


class ActionExtractor(Protocol):
    name: str

    def extract(
        self,
        message: str,
        now: datetime,
        timezone: str,
        history: list[dict[str, str]] | None = None,
    ) -> AssistantAction: ...


class RuleBasedExtractor:
    name = "rule_based"

    def extract(
        self,
        message: str,
        now: datetime,
        timezone: str,
        history: list[dict[str, str]] | None = None,
    ) -> AssistantAction:
        lowered = message.lower()
        local_now = ensure_aware(now, timezone)

        if any(word in lowered for word in ["cancel", "delete", "remove"]):
            uid = extract_booking_uid(message)
            return AssistantAction(
                intent=Intent.CANCEL,
                booking_uid=uid,
                reason="User requested cancellation",
                missing_fields=[] if uid else ["booking_uid"],
            )

        if any(word in lowered for word in ["reschedule", "move", "push", "change"]):
            uid = extract_booking_uid(message)
            lookup_start, start = parse_reschedule_times(message, local_now, timezone)
            missing = []
            if not uid:
                missing.append("booking_uid")
            if not start:
                missing.append("start")
            return AssistantAction(
                intent=Intent.RESCHEDULE,
                booking_uid=uid,
                lookup_start=lookup_start,
                start=start,
                reason="User requested reschedule",
                missing_fields=missing,
            )

        if any(phrase in lowered for phrase in ["what's on", "what is on", "show", "list", "scheduled", "calendar"]):
            date_from, date_to = day_bounds(parse_start(message, local_now, timezone) or local_now, timezone)
            return AssistantAction(intent=Intent.LIST, date_from=date_from, date_to=date_to)

        start = parse_start(message, local_now, timezone)
        attendee_email = extract_email(message)
        attendee_name = extract_name(message)
        missing = []
        if not start:
            missing.append("start")
        if not attendee_name:
            missing.append("attendee_name")
        if not attendee_email:
            missing.append("attendee_email")
        return AssistantAction(
            intent=Intent.BOOK,
            title=extract_title(message),
            start=start,
            duration_minutes=parse_duration(message),
            attendee_name=attendee_name,
            attendee_email=attendee_email,
            missing_fields=missing,
        )


class OpenAIExtractor:
    name = "openai"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def extract(
        self,
        message: str,
        now: datetime,
        timezone: str,
        history: list[dict[str, str]] | None = None,
    ) -> AssistantAction:
        if not self.settings.openai_api_key:
            raise LlmExtractionError("OPENAI_API_KEY is not configured")
        payload = {
            "model": self.settings.openai_model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Extract exactly one scheduling action from the user message. "
                        "Return JSON with keys: intent, title, start, duration_minutes, attendee_name, "
                        "attendee_email, guest_emails, booking_uid, reason, date_from, date_to, missing_fields. "
                        "intent must be one of book, list, cancel, reschedule, clarify. "
                        "Use timezone-aware ISO datetimes. Do not invent missing attendee emails, times, or booking UIDs. "
                        "For multiple attendees, put the primary attendee in attendee_email and extra attendee emails in guest_emails. "
                        "Important: if the user is clearly trying to book, cancel, or reschedule, keep that intent "
                        "even when required fields are missing; list missing fields in missing_fields. "
                        "Use intent=clarify only when the user's goal itself is unclear."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps({"message": message, "now": now.isoformat(), "timezone": timezone}),
                },
            ],
        }
        if history:
            payload["messages"].insert(
                1,
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "recent_conversation": history[-8:],
                            "instruction": (
                                "Use this recent conversation as short-term context. "
                                "If the latest user message fills a missing field from an earlier scheduling request, "
                                "merge it with that earlier request."
                            ),
                        }
                    ),
                },
            )
        try:
            headers = {
                "Authorization": f"Bearer {self.settings.openai_api_key}",
                "Content-Type": "application/json",
            }
            if self.settings.llm_provider == "openrouter":
                headers.update({
                    "HTTP-Referer": "http://127.0.0.1:8000",
                    "X-Title": "CalAssist",
                })
            response = httpx.post(
                f"{self.settings.openai_base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=20,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            data = json.loads(content)
            data = sanitize_action_data(data)
            return AssistantAction(**data)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise LlmExtractionError(
                    f"{self.settings.llm_provider} rate limit or quota was reached. "
                    "Please wait and retry, or use a key/model with available quota."
                ) from exc
            raise LlmExtractionError(
                f"{self.settings.llm_provider} extraction failed with HTTP {exc.response.status_code}: {exc.response.text}"
            ) from exc
        except (httpx.HTTPError, KeyError, json.JSONDecodeError, ValueError) as exc:
            raise LlmExtractionError(f"{self.settings.llm_provider} extraction failed: {exc}") from exc


def build_extractor(settings: Settings) -> ActionExtractor:
    if settings.llm_provider in {"openai", "openrouter"}:
        extractor = OpenAIExtractor(settings)
        extractor.name = settings.llm_provider
        return extractor
    return RuleBasedExtractor()


def sanitize_action_data(data: dict) -> dict:
    optional_fields = [
        "title",
        "start",
        "duration_minutes",
        "attendee_name",
        "attendee_email",
        "guest_emails",
        "booking_uid",
        "lookup_start",
        "reason",
        "date_from",
        "date_to",
    ]
    for field in optional_fields:
        if data.get(field) == "":
            data[field] = None
    if data.get("guest_emails") is None:
        data["guest_emails"] = []
    missing_fields = data.get("missing_fields")
    if missing_fields in (None, ""):
        data["missing_fields"] = []
    elif isinstance(missing_fields, str):
        data["missing_fields"] = [missing_fields]
    return data


def extract_booking_uid(text: str) -> str | None:
    blocked = {
        "cancel",
        "delete",
        "remove",
        "reschedule",
        "move",
        "push",
        "change",
        "today",
        "tomorrow",
        "booking",
        "uid",
    }
    explicit = re.search(r"\b(?:booking|uid)\s+([A-Za-z0-9_-]{3,})\b", text, flags=re.IGNORECASE)
    if explicit:
        return explicit.group(1)
    for token in re.findall(r"\b[A-Za-z0-9_-]{3,}\b", text):
        lowered = token.lower()
        if re.fullmatch(r"\d{1,2}(am|pm)", lowered):
            continue
        if re.fullmatch(r"\d{1,4}", lowered):
            continue
        if lowered not in blocked and ("_" in token or "-" in token or any(char.isdigit() for char in token)):
            return token
    return None


def extract_email(text: str) -> str | None:
    emails = extract_emails(text)
    return emails[0] if emails else None


def extract_emails(text: str) -> list[str]:
    return re.findall(r"[\w.\-+]+@[\w.\-]+\.\w+", text)


def is_valid_email(text: str | None) -> bool:
    if not text:
        return False
    return re.fullmatch(r"[\w.\-+]+@[\w.\-]+\.\w+", text.strip()) is not None


def extract_name(text: str) -> str | None:
    match = re.search(r"\bwith\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)?)\b", text)
    if match:
        return match.group(1)
    return None


def extract_title(text: str) -> str:
    lowered = text.lower()
    if "intro" in lowered:
        return "Intro call"
    if "candidate" in lowered:
        return "Candidate interview"
    if "lunch" in lowered:
        return "Lunch"
    return "Meeting"
