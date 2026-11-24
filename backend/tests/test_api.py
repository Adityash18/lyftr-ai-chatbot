import os
import tempfile
from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/lyftr_test.db")
os.environ.setdefault("LLM_API_KEYS", "test-key-one,test-key-two")
os.environ.setdefault("ENABLE_FAILURE_SIMULATION", "true")

from app.main import app, simulated_status

client = TestClient(app)

def test_health_contract():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["keys_configured"] >= 2
    assert response.json()["cache_enabled"] is True

def test_simulation_parser():
    assert simulated_status("key_1=429,key_2=500", "key_1") == 429
    assert simulated_status("*=429", "key_2") == 429
    assert simulated_status("key_1=429", "key_2") is None

def test_validation_rejects_blank_message():
    response = client.post("/chat", json={"session_id": "test-session", "message": "   "})
    assert response.status_code == 422
