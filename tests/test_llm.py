from app.llm import sanitize_action_data
from app.models import AssistantAction


def test_sanitize_action_data_converts_empty_optional_fields() -> None:
    data = sanitize_action_data(
        {
            "intent": "book",
            "title": "Meeting",
            "start": "2026-06-20T11:00:00-04:00",
            "attendee_name": "Trump",
            "attendee_email": "dtrump@whitehouse.com",
            "date_from": "",
            "date_to": "",
            "missing_fields": "",
        }
    )

    action = AssistantAction(**data)

    assert action.date_from is None
    assert action.date_to is None
    assert action.missing_fields == []

