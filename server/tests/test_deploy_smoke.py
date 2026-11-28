"""部署验收工具必须识别假成功，并且不得输出凭据或业务响应。"""

import copy
import importlib.util
import json
import warnings
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "deploy_smoke.py"
spec = importlib.util.spec_from_file_location("deploy_smoke", SCRIPT)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def server(*, health=None, constant=False, invalid_code=422, parse=None):
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.path == "/v1/health":
            return httpx.Response(200, json=health or {
                "status": "ok", "checks": {"schema": "ok", "config": "ok"},
            })
        body = json.loads(request.content)
        if request.url.path == "/v1/parse":
            return httpx.Response(200, content=json.dumps(parse), headers={"content-type": "application/json"})
        if not body["plan"].get("nodes"):
            return httpx.Response(invalid_code, json={"detail": "synthetic-private-response"})
        probability = 0.5 if constant else float(body["depart_min"] < 480)
        return httpx.Response(200, json={"sim": {
            "p_all": probability, "per_node": [{"node": "n1", "p_ok": probability}],
        }})

    return httpx.Client(base_url="https://synthetic.example", transport=httpx.MockTransport(handle)), calls


def test_real_checks_include_schema_config_and_variable_engine_input(capsys):
    client, calls = server()
    smoke.run_checks(client)
    assert len(calls) == 4
    output = capsys.readouterr().out
    assert "PASS" in output and "未运行 parse" in output


@pytest.mark.parametrize("checks", [
    {"schema": "missing", "config": "ok"},
    {"schema": "ok", "config": "missing:llm_key"},
    {"schema": "ok"},
])
def test_unhealthy_configuration_is_not_success(checks):
    client, calls = server(health={"status": "ok", "checks": checks})
    with pytest.raises(smoke.SmokeError):
        smoke.run_checks(client)
    assert len(calls) == 1


def test_mock_requires_explicit_opt_in_and_is_labelled(capsys):
    client, _ = server(health={"status": "ok", "checks": {"schema": "ok", "mode": "mock"}})
    with pytest.raises(smoke.SmokeError):
        smoke.run_checks(client)
    smoke.run_checks(client, allow_mock=True)
    assert "MOCK" in capsys.readouterr().out


@pytest.mark.parametrize("kwargs", [{"constant": True}, {"invalid_code": 200}])
def test_static_mock_response_or_accepted_invalid_plan_is_not_success(kwargs):
    client, _ = server(**kwargs)
    with pytest.raises(smoke.SmokeError):
        smoke.run_checks(client)


def test_parse_without_complete_plan_fails_without_printing_response(capsys):
    client, _ = server(parse={"draft_plan": {"private": "synthetic-private-response"}, "questions": []})
    with pytest.raises(smoke.SmokeError):
        smoke.run_checks(client, parse_request={"text": "synthetic text", "now": "2026-11-28T12:00:00+08:00"})
    assert "synthetic-private-response" not in capsys.readouterr().out


def real_plan():
    plan = smoke.simulation_request()["plan"]
    plan["nodes"][0]["loc"] = {"lng": 120.1, "lat": 30.2}
    plan["nodes"][1]["loc"] = {"lng": 120.11, "lat": 30.21}
    plan["edges"][0]["src"] = {"tag": "amap_route", "fetched_at": "2026-11-28T12:00:00+08:00"}
    return plan


def test_parse_plan_is_schema_checked_then_simulated(capsys):
    plan = real_plan()
    client, calls = server(parse={"draft_plan": plan, "questions": []})
    smoke.run_checks(client, parse_request={"text": "synthetic text", "now": "2026-11-28T12:00:00+08:00"})
    assert len(calls) == 6
    assert json.loads(calls[-1].content)["plan"] == plan
    assert "PASS /v1/parse" in capsys.readouterr().out


def test_real_health_with_old_parse_example_cannot_pass(capsys):
    example = json.loads((SCRIPT.parents[1] / "schema/examples/parse.json").read_text("utf-8"))["response"]
    client, calls = server(parse=example)
    with pytest.raises(smoke.SmokeError):
        smoke.run_checks(client, parse_request={"text": "synthetic text"})
    assert len(calls) == 5
    assert "真实部署与录入链路冒烟通过" not in capsys.readouterr().out


@pytest.mark.parametrize("section,index,field,value", [
    ("nodes", 0, "loc", None),
    ("nodes", 0, "loc", {"lng": 181, "lat": 0}),
    ("nodes", 0, "loc", {"lng": 0, "lat": -91}),
    ("nodes", 0, "loc", {"lng": float("nan"), "lat": 0}),
    ("nodes", 0, "loc", {"lng": True, "lat": 0}),
    ("edges", 0, "src", {"tag": "default"}),
    ("edges", 0, "src", {"tag": "amap_route", "fetched_at": "2026-11-28T12:00:00"}),
    ("edges", 0, "src", {"tag": "amap_route", "fetched_at": "invalid"}),
    ("edges", 0, "eta_min", 0),
    ("edges", 0, "eta_min", float("inf")),
    ("edges", 0, "dist_m", 0),
    ("edges", 0, "dist_m", float("nan")),
    ("edges", 0, "dist_m", -1),
])
def test_real_parse_requires_valid_locations_and_route_evidence(section, index, field, value):
    plan = real_plan()
    plan[section][index][field] = value
    client, _ = server(parse={"draft_plan": plan, "questions": []})
    with pytest.raises(smoke.SmokeError):
        smoke.run_checks(client, parse_request={"text": "synthetic text"})


def test_same_location_can_have_zero_route_metrics():
    plan = real_plan()
    plan["nodes"][1]["loc"] = copy.deepcopy(plan["nodes"][0]["loc"])
    plan["edges"][0].update(eta_min=0, dist_m=0)
    client, _ = server(parse={"draft_plan": plan, "questions": []})
    smoke.run_checks(client, parse_request={"text": "synthetic text"})


def test_mock_parse_example_remains_explicitly_mock(capsys):
    example = json.loads((SCRIPT.parents[1] / "schema/examples/parse.json").read_text("utf-8"))["response"]
    client, _ = server(health={"status": "ok", "checks": {"schema": "ok", "mode": "mock"}}, parse=example)
    smoke.run_checks(client, allow_mock=True, parse_request={"text": "synthetic text"})
    assert "MOCK 镜像冒烟通过" in capsys.readouterr().out


def test_main_env_token_is_header_only_and_failure_redacted(monkeypatch, capsys):
    secret = "synthetic-secret-token"
    monkeypatch.setenv("SUIBIAN_SMOKE_TOKEN", secret)
    calls = []

    def request(req):
        calls.append(req)
        return httpx.Response(401, json={"detail": secret})

    factory = httpx.Client
    monkeypatch.setattr(smoke.httpx, "Client", lambda **kwargs: factory(
        **kwargs, transport=httpx.MockTransport(request),
    ))
    assert smoke.main(["https://synthetic.example"]) == 1
    assert calls[0].headers["authorization"] == "Bearer " + secret
    assert secret not in capsys.readouterr().out


def test_login_uses_hidden_password_and_no_registration(monkeypatch, capsys):
    secret = "synthetic-password-123"
    monkeypatch.delenv("SUIBIAN_SMOKE_TOKEN", raising=False)
    monkeypatch.setattr("builtins.input", lambda _: "synthetic-user")
    monkeypatch.setattr(smoke.getpass, "getpass", lambda _: secret)
    client, calls = server()
    handler = client._transport.handler

    def request(req):
        if req.url.path == "/v1/auth/login":
            assert json.loads(req.content)["password"] == secret
            return httpx.Response(200, json={"access_token": "synthetic-session"})
        assert req.url.path != "/v1/auth/register"
        return handler(req)

    factory = httpx.Client
    monkeypatch.setattr(smoke.httpx, "Client", lambda **kwargs: factory(
        **kwargs, transport=httpx.MockTransport(request),
    ))
    assert smoke.main(["https://synthetic.example", "--login"]) == 0
    assert calls[-1].headers["authorization"] == "Bearer synthetic-session"
    assert secret not in capsys.readouterr().out


def test_login_refuses_echoed_password_fallback(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda _: "synthetic-user")

    def no_terminal(_):
        warnings.warn("No hidden input", smoke.getpass.GetPassWarning, stacklevel=2)

    monkeypatch.setattr(smoke.getpass, "getpass", no_terminal)
    assert smoke.main(["https://synthetic.example", "--login"]) == 1
    assert "无法隐藏密码" in capsys.readouterr().out


@pytest.mark.parametrize("base", ["https://user:pass@synthetic.example", "https://synthetic.example?token=secret"])
def test_base_url_cannot_carry_credentials_or_queries(base, capsys):
    assert smoke.main([base]) == 1
    assert "pass@" not in capsys.readouterr().out


def test_invalid_json_is_sanitized(monkeypatch, capsys):
    factory = httpx.Client
    monkeypatch.setattr(smoke.httpx, "Client", lambda **kwargs: factory(
        **kwargs, transport=httpx.MockTransport(lambda _: httpx.Response(200, text="synthetic-private-response")),
    ))
    assert smoke.main(["https://synthetic.example"]) == 1
    assert "synthetic-private-response" not in capsys.readouterr().out


def test_json_null_parse_file_cannot_silently_skip_parse(tmp_path, capsys):
    path = tmp_path / "request.json"
    path.write_text("null", encoding="utf-8")
    assert smoke.main(["https://synthetic.example", "--parse-file", str(path)]) == 1
    assert "必须是 JSON 对象" in capsys.readouterr().out


def test_smoke_against_real_engine_with_required_auth(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MOCK", "1")
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            result = client.post("/v1/auth/register", json={
                "username": "smoke-user", "password": "synthetic-password-123",
            })
            assert result.status_code == 201
            client.headers["Authorization"] = "Bearer " + result.json()["access_token"]
            smoke.run_checks(client, allow_mock=True)
        assert "MOCK 镜像冒烟通过" in capsys.readouterr().out
    finally:
        get_settings.cache_clear()
