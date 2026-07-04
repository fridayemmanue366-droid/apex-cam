from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_capabilities_shape():
    r = client.get("/capabilities")
    assert r.status_code == 200
    body = r.json()
    assert "gpu" in body
    assert "target" in body


def test_face_roundtrip():
    r = client.put("/face", json={"enabled": True, "model": "passthrough",
                                  "swap_enabled": False, "profile_id": None})
    assert r.status_code == 200
    assert r.json()["enabled"] is True
