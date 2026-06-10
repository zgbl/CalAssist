from fastapi.testclient import TestClient

from app.cal_client import MockCalClient
from app.config import Settings
from app.main import app
from app.main import get_cal_gateway, get_settings


mock = MockCalClient()
app.dependency_overrides[get_cal_gateway] = lambda: mock
app.dependency_overrides[get_settings] = lambda: Settings(llm_provider="rule_based", cal_api_key=None)
client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_chat_endpoint() -> None:
    response = client.post(
        "/chat",
        json={
            "conversation_id": "api-test",
            "message": "what's on my calendar tomorrow?",
            "timezone": "America/New_York",
            "now": "2026-06-10T10:00:00-04:00",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
