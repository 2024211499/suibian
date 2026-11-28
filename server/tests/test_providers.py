"""团队凭据的访问控制、加密、校验与即时生效；全部使用合成密钥。"""

import json

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app

KEY = "synthetic-provider-key-only-for-tests"


@pytest.fixture
def admin(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    monkeypatch.setenv("AUTH_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("AUTH_ADMIN_PASSWORD", "test-admin-password!")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            response = client.post("/v1/auth/login", json={"username": "admin", "password": "test-admin-password!"})
            assert response.status_code == 200
            client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
            yield client, tmp_path
    finally:
        get_settings.cache_clear()


def test_admin_config_is_encrypted_and_not_returned(admin):
    client, path = admin
    response = client.put("/v1/admin/providers/qwen", json={"key": KEY, "main_model": "synthetic-model"})
    assert response.status_code == 200
    assert KEY not in response.text
    providers = client.get("/v1/admin/providers")
    assert providers.status_code == 200
    assert KEY not in providers.text
    assert providers.json()["providers"]["qwen"]["configured"] is True
    assert KEY.encode() not in (path / "providers.sqlite3").read_bytes()
    assert client.app.state.provider_store.effective_settings().qwen_key == KEY


@pytest.mark.parametrize("base", ["http://api.deepseek.com", "https://evil.example", "https://api.deepseek.com@evil.example"])
def test_provider_rejects_nonofficial_or_insecure_address(admin, base):
    client, _ = admin
    response = client.put("/v1/admin/providers/deepseek", json={"key": KEY, "main_model": "test", "base_url": base})
    assert response.status_code == 422
    assert KEY not in response.text


def test_config_requires_valid_model_and_key(admin):
    client, _ = admin
    for payload in ({"key": ""}, {"key": KEY, "main_model": ""}, {"key": KEY, "main_model": "bad\nmodel"}):
        response = client.put("/v1/admin/providers/deepseek", json=payload)
        assert response.status_code == 422
        assert KEY not in response.text


def test_key_persists_across_app_restart(admin):
    client, _ = admin
    assert client.put("/v1/admin/providers/amap", json={"key": KEY}).status_code == 200
    with TestClient(create_app()) as second:
        assert second.app.state.provider_store.effective_settings().amap_web_key == KEY


def test_model_probe_tests_only_selected_provider(admin, monkeypatch):
    client, _ = admin
    client.put("/v1/admin/providers/deepseek", json={"key": KEY, "main_model": "synthetic-model"})
    seen = []

    async def probe(self):
        seen.extend(provider.name for provider in self.providers)
        return {"deepseek": "ok"}

    monkeypatch.setattr("app.llm.client.LLMClient.probe", probe)
    response = client.post("/v1/admin/providers/deepseek/test", json={})
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert seen == ["deepseek"]


def test_map_probe_uses_saved_key(admin, monkeypatch):
    client, _ = admin
    client.put("/v1/admin/providers/amap", json={"key": KEY})
    seen = []

    async def geocode(address, city):
        from app.config import runtime_settings
        seen.append(runtime_settings().amap_web_key)
        return {"adcode": "110101", "level": "兴趣点"}

    monkeypatch.setattr("app.gateway.amap.geocode", geocode)
    response = client.post("/v1/admin/providers/amap/test", json={"address": "合成测试地点", "city_adcode": "110100"})
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert seen == [KEY]
    assert KEY not in json.dumps(response.json())


def test_missing_encryption_key_reports_unavailable_without_fallback(admin):
    client, path = admin
    client.put("/v1/admin/providers/amap", json={"key": KEY})
    (path / "provider.key").unlink()
    response = client.get("/v1/admin/providers")
    assert response.status_code == 503
    assert KEY not in response.text


def test_invalid_key_and_unprivileged_updates_are_rejected(admin):
    client, _ = admin
    response = client.put("/v1/admin/providers/amap", json={"key": "hidden secret with spaces"})
    assert response.status_code == 422
    assert "hidden secret" not in response.text
    client.headers.pop("Authorization")
    assert client.put("/v1/admin/providers/amap", json={"key": KEY}).status_code == 401
