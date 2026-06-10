from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.agent import ChatMemory, SchedulingAssistant
from app.cal_client import CalClient, CalGateway, MockCalClient
from app.config import Settings, get_settings
from app.llm import build_extractor
from app.models import BookingSummary, ChatRequest, ChatResponse


app = FastAPI(title="CalAssist", description="Conversational scheduling assistant for Cal.com", version="0.1.0")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

memory = ChatMemory()
mock_client = MockCalClient()


def get_cal_gateway(settings: Settings = Depends(get_settings)) -> CalGateway:
    if settings.has_cal_credentials:
        return CalClient(settings)
    return mock_client


def get_assistant(settings: Settings = Depends(get_settings), cal: CalGateway = Depends(get_cal_gateway)) -> SchedulingAssistant:
    return SchedulingAssistant(cal=cal, extractor=build_extractor(settings), settings=settings, memory=memory)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (Path(__file__).parent / "static" / "index.html").read_text()


@app.get("/health")
def health(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    return {
        "status": "ok",
        "cal_configured": settings.has_cal_credentials,
        "booking_target_configured": settings.has_booking_target,
        "llm_provider": settings.llm_provider,
    }


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, assistant: SchedulingAssistant = Depends(get_assistant)) -> ChatResponse:
    return assistant.handle(request)


@app.get("/api/bookings", response_model=list[BookingSummary])
def bookings(cal: CalGateway = Depends(get_cal_gateway)) -> list[BookingSummary]:
    return cal.list_bookings(status="upcoming")


@app.get("/api/event-types")
def event_types(cal: CalGateway = Depends(get_cal_gateway)) -> list[dict]:
    return cal.list_event_types()
