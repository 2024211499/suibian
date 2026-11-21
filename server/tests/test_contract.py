"""跨端契约测试 —— 审计 P1-3 的验收：
生成 → 取结果（轮询）→ 选中计划直接交给推演和重排，全程结构合法。

跑在 mock 模式下，但 simulate 对完整计划图跑的是真引擎——这条链路测的是契约和引擎，
不是示例 JSON。
"""

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from app.config import get_settings
from app.main import create_app

PLAN_SCHEMA = json.loads(
    (Path(__file__).resolve().parents[2] / "schema" / "plan_graph.schema.json").read_text("utf-8")
)


def test_generate_to_result_to_simulate_to_replan_chain():
    client = TestClient(create_app())

    # 1. 生成 → job_id
    job = client.post(
        "/v1/generate",
        json={"text": "周六去西湖玩一天，晚上回来", "now": "2026-12-03T20:00:00+08:00"},
    ).json()
    assert job.get("job_id"), job

    # 2. 轮询取结果：done 语义明确，候选结构完整
    res = client.get(f"/v1/generate/{job['job_id']}").json()
    assert res.get("done") is True, res
    candidates = res.get("candidates") or []
    assert len(candidates) >= 2
    for c in candidates:
        # 每个"可采纳方案"必须带完整计划图，且通过 plan_graph.schema 校验
        Draft202012Validator(PLAN_SCHEMA).validate(c["plan"])
    stream = client.get(f"/v1/generate/{job['job_id']}/events").text
    assert [json.loads(x) for x in re.findall(r"event: option\r?\ndata: (.+)", stream)] == candidates
    assert json.loads(re.search(r"event: done\r?\ndata: (.+)", stream)[1])["candidates"] == candidates

    # 3. 选中计划直接交给推演（mock 下走真引擎）
    plan = candidates[0]["plan"]
    sim = client.post(
        "/v1/simulate",
        json={"plan": plan, "profile": {}, "depart_min": 480, "seed": 42},
    ).json()
    assert 0.0 <= sim["sim"]["p_all"] <= 1.0
    assert isinstance(sim["sim"]["per_node"], list)  # 契约统一为列表（技术文档 2.2）

    # 4. 同一张计划图交给重排：每个方案的 plan_after 也是完整计划图
    rep = client.post(
        "/v1/replan",
        json={
            "plan": plan, "depart_min": 480, "seed": 42,
            "trigger": {"kind": "weather", "data": {"edge_id": "e1", "delay_min": 15}},
        },
    ).json()
    assert rep.get("options"), rep
    for o in rep["options"]:
        Draft202012Validator(PLAN_SCHEMA).validate(o["plan_after"])
        assert o["plan_after"]["nodes"] and o["plan_after"]["edges"]
        assert o["plan_after"]["plan_id"] == plan["plan_id"]
        assert o["plan_after"]["version"] == plan["version"] + 1
    assert rep["affected"][0]["node"] == "n1"
    assert rep["affected"][0]["before"]["arrive_p90"] < rep["affected"][0]["after"]["arrive_p90"]


def test_simulate_rejects_dangling_edge_with_422():
    """引擎在计算前拒绝悬空边，接口层表现为 422 而不是 500。"""
    client = TestClient(create_app())
    bad = {
        "depart_min": 480,
        "plan": {
            "plan_id": "p_bad",
            "nodes": [
                {"id": "n0", "kind": "origin", "name": "家"},
                {"id": "n1", "kind": "stay", "name": "地"},
            ],
            "edges": [{"id": "e1", "from": "n0", "to": "nX", "mode": "walk", "eta_min": 10}],
        }
    }
    r = client.post("/v1/simulate", json=bad)
    assert r.status_code == 422
    assert "悬空边" in r.json()["detail"]


def test_live_simulation_uses_supplied_departure(monkeypatch):
    monkeypatch.setenv("MOCK", "0")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            fixture = Path(__file__).resolve().parents[2] / "schema" / "examples" / "simulate.json"
            request = json.loads(fixture.read_text("utf-8"))["request"]
            early = client.post("/v1/simulate", json={**request, "depart_min": 420})
            late = client.post("/v1/simulate", json={**request, "depart_min": 500})
            assert early.status_code == late.status_code == 200
            assert early.json()["sim"]["p_all"] > late.json()["sim"]["p_all"]
            broken = {**request, "plan": {**request["plan"], "edges": []}}
            assert client.post("/v1/simulate", json=broken).status_code == 422
    finally:
        get_settings.cache_clear()
