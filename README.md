# CalAssist

Conversational scheduling assistant for Cal.com.

The user can manage their Cal.com bookings through plain English:

- Book a new event
- See scheduled events
- Cancel a booking
- Reschedule a booking
- Use a minimal interactive web UI

The app is written in Python with FastAPI. The real application path uses an OpenAI-compatible LLM for conversational understanding and the Cal.com REST API for calendar operations. Tests can run with mocked Cal.com calls and a rule-based extractor so they are deterministic and do not require external services.

## Architecture

```text
Web UI / curl
  -> FastAPI /chat
  -> SchedulingAssistant
  -> ActionExtractor
       - OpenAI-compatible JSON extraction for the real app
       - rule_based extractor only for deterministic tests
  -> CalGateway
       - MockCalClient without credentials
       - CalClient with Cal.com API key
  -> Cal.com REST API v2
```

Important design choices:

- The conversational layer extracts a structured action.
- Cal.com API calls are isolated in `app/cal_client.py`.
- The assistant keeps short-lived pending actions and recent conversation history when it needs missing details.
- Tests use `MockCalClient`, mocked HTTP calls, and deterministic extraction, so they do not need real API keys.
- Real Cal.com credentials are read from environment variables and are never committed.

## Cal.com API Usage

This project uses Cal.com API v2:

- `GET /v2/bookings` to list bookings
- `POST /v2/bookings` to create a booking
- `POST /v2/bookings/{bookingUid}/cancel` to cancel
- `POST /v2/bookings/{bookingUid}/reschedule` to reschedule
- `GET /v2/event-types` to discover event type ids

Headers:

```text
Authorization: Bearer <CAL_API_KEY>
cal-api-version: 2026-05-01 for listing bookings
cal-api-version: 2026-02-25 for create/cancel/reschedule
cal-api-version: 2024-06-14 for event type discovery
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

## Configure Cal.com

Create a `.env` file:

```bash
CAL_API_KEY="Your CAL.com API KEY"
CAL_DEFAULT_TIMEZONE=America/New_York
CAL_DEFAULT_ATTENDEE_NAME="YOUR NAME"
CAL_DEFAULT_ATTENDEE_EMAIL="your@email.com"
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY="Your Openrouter Key"
OPENROUTER_MODEL=nvidia/nemotron-3-super-120b-a12b:free

```

Configure the booking target using one of these options.

Option A, easiest:

```bash
CAL_EVENT_TYPE_ID=123
```

Option B:

```bash
CAL_EVENT_TYPE_SLUG=30min
CAL_USERNAME=your-cal-username
CAL_ORGANIZATION_SLUG=
```

Most Cal.com event types have a fixed length. For those, do not send `lengthInMinutes`; Cal.com will use the event type length. This app defaults to that behavior. Only set this if your event type supports multiple possible lengths:

```bash
CAL_SEND_LENGTH_IN_MINUTES=true
```

Cal.com validates bookings against the event type's public availability. For example, a brand-new account may have no conflicts but still reject a Saturday booking if the event type only accepts weekday bookings. Because this assistant acts on behalf of the calendar owner, the app defaults to allowing bookings outside public event-type availability:

```bash
CAL_ALLOW_BOOKING_OUT_OF_BOUNDS=true
CAL_ALLOW_CONFLICTS=false
```

`CAL_ALLOW_BOOKING_OUT_OF_BOUNDS=true` allows owner-created meetings on weekends or outside the public booking window. `CAL_ALLOW_CONFLICTS=false` keeps real calendar conflict protection enabled by default.

If `CAL_API_KEY` is missing, the app uses `MockCalClient` so the UI and tests still work. In mock mode, an event type is not required. With real Cal.com credentials, configure an event type before booking.

`.env` is ignored by Git and should not be committed.

## LLM Configuration

The submitted app is intended to run with an LLM:

```bash
LLM_PROVIDER=openai
OPENAI_API_KEY=your_openai_key
OPENAI_MODEL=gpt-4o-mini
```

Other OpenAI-compatible providers can be used by changing the base URL and model:

```bash
LLM_PROVIDER=openai
OPENAI_BASE_URL=https://openrouter.ai/api/v1
OPENAI_API_KEY=your_provider_key
OPENAI_MODEL=openai/gpt-4o-mini
```

OpenRouter can also be configured directly:

```bash
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=your_openrouter_key
OPENROUTER_MODEL=nvidia/nemotron-3-super-120b-a12b:free
```

The LLM is responsible for understanding the user's conversational request and extracting a structured scheduling action. Booking creation, cancellation, rescheduling, and API error handling remain deterministic backend logic.

`LLM_PROVIDER=rule_based` exists only as a deterministic test mode. It is not the intended production or submission configuration. In `LLM_PROVIDER=openai` or `LLM_PROVIDER=openrouter` mode, LLM extraction failures return an error instead of silently falling back to rule-based parsing.

The assistant also keeps a short in-memory conversation history per `conversation_id`, so follow-up messages like an attendee email can complete a booking request from the previous turn.

Successful chat responses include an `extractor` field. For the submitted configuration, it should be:

```json
{"extractor": "openai"}
```

For OpenRouter mode, it should be:

```json
{"extractor": "openrouter"}
```

## Run

```bash
uvicorn app.main:app --reload
```

Open:

```text
http://127.0.0.1:8000
```

API docs:

```text
http://127.0.0.1:8000/docs
```

List Cal.com event types to find `CAL_EVENT_TYPE_ID`:

```bash
curl -s http://127.0.0.1:8000/api/event-types
```

## Test

```bash
pytest
```

## Example curl

Check configuration:

```bash
curl -s http://127.0.0.1:8000/health
```

List bookings:

```bash
curl -s -X POST http://127.0.0.1:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "conversation_id": "demo",
    "message": "what is on my calendar tomorrow?",
    "timezone": "America/New_York"
  }'
```

Book a meeting:

```bash
curl -s -X POST http://127.0.0.1:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "conversation_id": "demo",
    "message": "book a 30-min intro with Alex alex@example.com tomorrow at 2pm",
    "timezone": "America/New_York",
    "now": "2026-06-10T10:00:00-04:00"
  }'
```

If the user does not specify a duration, the assistant defaults to 30 minutes and confirms that default in the reply. If the user does not specify a subject, the assistant defaults to `Meeting: <host> and <attendee>` and confirms that default.

For multiple attendees, the first email is sent as Cal.com's primary `attendee.email`, and additional emails are sent through Cal.com's `guests` field:

```text
Setup a new meeting Jun 16 11AM with Trump (dtrump@example.com) and Rubio (rubio@example.com)
```

This becomes:

```json
{
  "attendee": {"email": "dtrump@example.com"},
  "guests": ["rubio@example.com"]
}
```

Cancel a booking:

```bash
curl -s -X POST http://127.0.0.1:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "conversation_id": "demo",
    "message": "cancel <booking_uid_from_list_or_book_response>",
    "timezone": "America/New_York"
  }'
```

Reschedule a booking:

```bash
curl -s -X POST http://127.0.0.1:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "conversation_id": "demo",
    "message": "move <booking_uid_from_list_or_book_response> to tomorrow at 4pm",
    "timezone": "America/New_York",
    "now": "2026-06-10T10:00:00-04:00"
  }'
```

In real Cal.com mode, use an actual booking UID returned by Cal.com. `mock_1` only appears in local mock mode when `CAL_API_KEY` is not configured.

## Example Conversation

```text
User: Book a meeting with Alex tomorrow at 2pm
Assistant: What is the attendee's email address?
User: alex@example.com
Assistant: Booked Meeting: Xinyu Tu and Alex for 2026-06-11 14:00:00-04:00. Booking UID: mock_1
Confirmed: defaulted duration to 30 minutes; defaulted subject to 'Meeting: Xinyu Tu and Alex'.
User: what's on my calendar tomorrow?
Assistant: Upcoming bookings:
- mock_1: Meeting: Xinyu Tu and Alex at 2026-06-11 14:00:00-04:00 (accepted)
User: move mock_1 to tomorrow at 4pm
Assistant: Rescheduled booking mock_1 to 2026-06-11 16:00:00-04:00.
```

The example above uses `mock_1` to illustrate local mock mode. In real Cal.com mode, the booking UID will be a Cal.com UID returned by the booking API.

## Limitations

- The real conversational path requires an OpenAI-compatible LLM. The rule-based extractor is intentionally simple and exists only for deterministic tests.
- The web UI is minimal but usable.
- This submission assumes one configured Cal.com event type.
- Bookings are still subject to Cal.com event type rules. The app defaults `CAL_ALLOW_BOOKING_OUT_OF_BOUNDS=true` so owner-created meetings can be placed outside public booking hours, but `CAL_ALLOW_CONFLICTS=false` keeps calendar conflict checks enabled.
- It does not implement OAuth because a personal API key is sufficient for this challenge.
- It stores only short in-memory conversation history and pending actions; it does not persist chat sessions across restarts.
- Slot lookup is not required for the main flow, but would be a natural next step before confirming a proposed time.

## Future Improvements

- Add Cal.com slot lookup before booking to suggest valid times.
- Add persistent chat sessions.
- Add OAuth for multi-user deployment.
- Add stronger LLM schema enforcement and prompt versioning.
- Add observability around intent extraction and Cal.com API failures.
