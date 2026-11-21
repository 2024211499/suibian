"""接口烟囱测试：mock 模式下 health 和核心接口可访问，examples 响应结构完整。"""

from fastapi.testclient import TestClient

from app.main import create_app


def test_health_ok_in_mock():
    client = TestClient(create_app())
    r = client.get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["checks"]["mode"] == "mock"


def test_parse_mock_returns_intent_and_plan():
    client = TestClient(create_app())
    r = client.post("/v1/parse", json={"text": "明早8点三教上课", "now": "2026-12-04T21:00:00+08:00"})
    assert r.status_code == 200
    body = r.json()
    assert "intent" in body and "draft_plan" in body
    assert body["draft_plan"]["nodes"][0]["kind"] == "origin"


def test_simulate_mock_returns_p_all():
    client = TestClient(create_app())
    r = client.post("/v1/simulate", json={"plan": {"plan_id": "p_demo01"}, "profile": {}})
    assert r.status_code == 200
    assert 0.0 <= r.json()["sim"]["p_all"] <= 1.0


def test_generate_events_mock_streams_sse():
    client = TestClient(create_app())
    with client.stream("GET", "/v1/generate/job_demo/events") as resp:
        assert resp.status_code == 200
        lines = "".join(resp.iter_text())
    assert "event: stage" in lines and "event: done" in lines


def test_unimplemented_returns_501_when_not_mock(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("MOCK", "0")
    get_settings.cache_clear()
    try:
        client = TestClient(create_app())
        r = client.post("/v1/parse", json={"text": "x", "now": "2026-12-04T21:00:00+08:00"})
        assert r.status_code == 501
        assert "todo" in r.json()
    finally:
        get_settings.cache_clear()
