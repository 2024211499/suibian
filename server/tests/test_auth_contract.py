"""账号模块验收契约：先确认预期，再实现；不访问供应商服务。"""

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app

PASSWORD = "test-only-password-482!"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("MOCK", "1")
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("AUTH_ADMIN_USERNAME", "")
    monkeypatch.setenv("AUTH_ADMIN_PASSWORD", "")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as test_client:
            yield test_client
    finally:
        get_settings.cache_clear()


def register(client, username="tester"):
    return client.post("/v1/auth/register", json={"username": username, "password": PASSWORD})


def bearer(response):
    assert response.status_code in (200, 201)
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_registration_returns_user_session_without_password(client):
    response = register(client)
    assert response.status_code == 201
    body = response.json()
    assert body["token_type"] == "bearer"
    assert 0 < body["expires_in"] <= 86400
    assert body["user"]["username"] == "tester"
    assert body["user"]["role"] == "user"
    assert isinstance(body["access_token"], str) and len(body["access_token"]) >= 32
    assert PASSWORD not in response.text
    assert "password" not in body["user"]


def test_username_is_case_insensitive_and_unique(client):
    bearer(register(client, "Tester"))
    response = register(client, "tester")
    assert response.status_code == 409


@pytest.mark.parametrize("password", ["", "x7!y2", "x" * 129])
def test_invalid_password_is_rejected_without_echo(client, password):
    response = client.post("/v1/auth/register", json={"username": "tester", "password": password})
    assert response.status_code == 422
    if password:
        assert password not in response.text


def test_registration_cannot_assign_admin_role(client):
    response = client.post(
        "/v1/auth/register", json={"username": "tester", "password": PASSWORD, "role": "admin"}
    )
    assert response.status_code == 422


def test_login_and_current_user(client):
    bearer(register(client))
    login = client.post("/v1/auth/login", json={"username": "TESTER", "password": PASSWORD})
    response = client.get("/v1/auth/me", headers=bearer(login))
    assert response.status_code == 200
    assert response.json()["username"] == "tester"
    assert response.json()["role"] == "user"


def test_failed_login_does_not_reveal_account_existence(client):
    bearer(register(client))
    responses = [
        client.post("/v1/auth/login", json={"username": username, "password": "wrong-password"})
        for username in ("tester", "absent")
    ]
    assert [response.status_code for response in responses] == [401, 401]
    assert responses[0].json() == responses[1].json()


def test_logout_revokes_only_the_current_session(client):
    first = bearer(register(client))
    second = bearer(client.post("/v1/auth/login", json={"username": "tester", "password": PASSWORD}))
    assert client.post("/v1/auth/logout", headers=first).status_code == 204
    assert client.get("/v1/auth/me", headers=first).status_code == 401
    assert client.get("/v1/auth/me", headers=second).status_code == 200


@pytest.mark.parametrize("path", ["/v1/auth/me", "/v1/admin/providers"])
def test_private_endpoints_require_login(client, path):
    assert client.get(path).status_code == 401


def test_ordinary_user_cannot_manage_team_credentials(client):
    headers = bearer(register(client))
    assert client.get("/v1/admin/providers", headers=headers).status_code == 403


def test_existing_parse_requires_login_when_auth_enabled(client):
    payload = {"text": "明天上午八点去上课", "now": "2026-11-28T10:00:00+08:00"}
    assert client.post("/v1/parse", json=payload).status_code == 401
    response = client.post("/v1/parse", json=payload, headers=bearer(register(client)))
    assert response.status_code == 200
    assert set(response.json()) == {"intent", "draft_plan", "questions"}


def test_health_remains_public(client):
    assert client.get("/v1/health").status_code == 200


@pytest.mark.parametrize("path", ["simulate", "replan", "ocr/parse", "generate", "metrics"])
def test_business_routes_cannot_bypass_auth(client, path):
    assert client.post(f"/v1/{path}", json={}).status_code == 401


def test_registration_rate_limit_does_not_trust_forwarded_header(client):
    for i in range(5):
        response = client.post("/v1/auth/register", json={"username": f"user{i}", "password": PASSWORD},
                               headers={"X-Forwarded-For": f"192.0.2.{i}"})
        assert response.status_code == 201
    response = register(client, "one-more")
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0


def test_personal_budget_prevents_model_call(client, monkeypatch):
    headers = bearer(register(client))
    monkeypatch.setenv("MOCK", "0")
    monkeypatch.setenv("PARSE_USER_DAILY_MAX", "0")
    get_settings.cache_clear()

    async def unexpected(*args, **kwargs):
        pytest.fail("个人额度耗尽后禁止调用模型")

    monkeypatch.setattr("app.llm.client.LLMClient.call", unexpected)
    response = client.post("/v1/parse", headers=headers, json={
        "text": "明天上午八点去上课", "now": "2026-11-28T10:00:00+08:00",
    })
    assert response.status_code == 200
    assert response.json()["draft_plan"] is None
    assert "个人解析额度" in response.json()["questions"][0]
