"""部署冒烟：凭据只经环境变量或隐藏输入读取，业务响应不写入终端。"""

import argparse
import copy
import getpass
import json
import math
import os
import sys
import warnings
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from jsonschema import Draft202012Validator, ValidationError

ROOT = Path(__file__).resolve().parents[1]


class SmokeError(Exception):
    pass


def simulation_request():
    payload = json.loads((ROOT / "schema/examples/simulate.json").read_text("utf-8"))["request"]
    return {"plan": payload["plan"], "depart_min": 420, "n": 128, "seed": 42, "use_weather": False}


def request_json(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    if response.status_code != 200:
        hint = "；请设置 SUIBIAN_SMOKE_TOKEN 或使用 --login" if response.status_code == 401 else ""
        raise SmokeError(f"{path} 返回 HTTP {response.status_code}{hint}")
    try:
        result = response.json()
    except ValueError:
        raise SmokeError(f"{path} 未返回合法 JSON") from None
    if not isinstance(result, dict):
        raise SmokeError(f"{path} 返回格式不正确")
    return result


def probability(result):
    sim = result.get("sim")
    if not isinstance(sim, dict) or not isinstance(sim.get("per_node"), list) or not sim["per_node"]:
        raise SmokeError("/v1/simulate 缺少节点推演结果")
    value = sim.get("p_all")
    if not finite_number(value) or not 0 <= value <= 1:
        raise SmokeError("/v1/simulate 缺少合法概率")
    return value


def finite_number(value):
    try:
        return not isinstance(value, bool) and isinstance(value, int | float) and math.isfinite(value)
    except OverflowError:
        return False


def validate_route_evidence(plan):
    coordinates = {}
    for node in plan["nodes"]:
        location = node.get("loc")
        if not isinstance(location, dict) or any(
            not finite_number(location.get(axis)) or not -limit <= location[axis] <= limit
            for axis, limit in (("lng", 180), ("lat", 90))
        ):
            raise SmokeError("/v1/parse 草案缺少有效地点坐标")
        coordinates[node["id"]] = (location["lng"], location["lat"])
    for edge in plan["edges"]:
        source = edge.get("src") or {}
        if source.get("tag") != "amap_route":
            raise SmokeError("/v1/parse 草案缺少真实高德路线来源")
        try:
            if datetime.fromisoformat(source.get("fetched_at")).utcoffset() is None:
                raise ValueError
        except (TypeError, ValueError):
            raise SmokeError("/v1/parse 路线取数时间缺失或未带有效时区") from None
        if edge["from"] not in coordinates or edge["to"] not in coordinates:
            raise SmokeError("/v1/parse 路线起终点不存在")
        same_location = coordinates[edge["from"]] == coordinates[edge["to"]]
        for field in ("eta_min", "dist_m"):
            value = edge.get(field)
            if not finite_number(value) or value < 0 or (value == 0 and not same_location):
                raise SmokeError("/v1/parse 路线耗时或距离缺失、无效")


def run_checks(client, *, allow_mock=False, parse_request=None):
    health = request_json(client, "GET", "/v1/health")
    checks = health.get("checks", {})
    if health.get("status") != "ok" or not isinstance(checks, dict) or checks.get("schema") != "ok":
        raise SmokeError("健康检查或 Schema 资源检查失败")
    mock = checks.get("mode") == "mock"
    if mock and not allow_mock:
        raise SmokeError("服务处于 MOCK 模式；仅测试镜像时使用 --allow-mock")
    if not mock and checks.get("config") != "ok":
        raise SmokeError("真实服务配置不完整")
    print("PASS /v1/health：Schema 与运行模式检查")

    payload = simulation_request()
    early = probability(request_json(client, "POST", "/v1/simulate", json=payload))
    late = probability(request_json(client, "POST", "/v1/simulate", json={**payload, "depart_min": 540}))
    if early <= late:
        raise SmokeError("改变出发时间后推演未体现迟到风险，不能证明真实引擎已运行")
    broken = copy.deepcopy(payload)
    broken["plan"]["nodes"] = []
    if client.post("/v1/simulate", json=broken).status_code != 422:
        raise SmokeError("/v1/simulate 未按契约拒绝无效计划")
    print("PASS /v1/simulate：出发时间影响结果，无效计划返回 422")

    if parse_request is not None:
        parsed = request_json(client, "POST", "/v1/parse", json=parse_request)
        plan = parsed.get("draft_plan")
        if not isinstance(plan, dict) or parsed.get("questions") != []:
            raise SmokeError("/v1/parse 尚未得到无需补充信息的完整草案")
        schema = json.loads((ROOT / "schema/plan_graph.schema.json").read_text("utf-8"))
        try:
            Draft202012Validator(schema).validate(plan)
        except ValidationError:
            raise SmokeError("/v1/parse 草案未通过计划 Schema") from None
        if not mock:
            validate_route_evidence(plan)
        probability(request_json(client, "POST", "/v1/simulate", json={**payload, "plan": plan}))
        print("PASS /v1/parse：完整草案通过 Schema，并可直接推演")
    if mock:
        print("MOCK 镜像冒烟通过；不代表真实外部服务验收通过")
    elif parse_request is None:
        print("基础部署冒烟通过；未运行 parse，请用 --parse-file 补测真实录入链路")
    else:
        print("真实部署与录入链路冒烟通过")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", nargs="?", default="http://127.0.0.1:8000")
    parser.add_argument("--allow-mock", action="store_true", help="仅用于 MOCK 镜像/契约检查")
    parser.add_argument("--login", action="store_true", help="交互登录已有账号；密码隐藏输入")
    parser.add_argument("--parse-file", type=Path, help="完整 parse 请求 JSON 文件（含当前 now 与明确起终点）")
    args = parser.parse_args(argv)
    try:
        url = urlsplit(args.base)
        if (
            url.scheme not in ("http", "https") or not url.hostname
            or url.username or url.password or url.query or url.fragment
        ):
            raise SmokeError("BASE 必须是无凭据、查询参数和片段的 HTTP(S) 地址")
        parse_request = json.loads(args.parse_file.read_text("utf-8")) if args.parse_file else None
        if args.parse_file and not isinstance(parse_request, dict):
            raise SmokeError("parse 请求文件必须是 JSON 对象")
        token = os.environ.get("SUIBIAN_SMOKE_TOKEN", "")
        with httpx.Client(base_url=args.base.rstrip("/"), timeout=40, follow_redirects=False) as client:
            if args.login:
                username = input("已有用户名：").strip()
                with warnings.catch_warnings():
                    warnings.simplefilter("error", getpass.GetPassWarning)
                    password = getpass.getpass("密码（隐藏输入）：")
                token = request_json(client, "POST", "/v1/auth/login", json={
                    "username": username, "password": password,
                }).get("access_token")
                if not isinstance(token, str) or not token:
                    raise SmokeError("登录响应缺少会话令牌")
            if token:
                client.headers["Authorization"] = f"Bearer {token}"
            run_checks(client, allow_mock=args.allow_mock, parse_request=parse_request)
        return 0
    except SmokeError as error:
        print(f"FAIL {error}")
    except getpass.GetPassWarning:
        print("FAIL 当前终端无法隐藏密码，请使用可交互终端或 SUIBIAN_SMOKE_TOKEN")
    except (httpx.HTTPError, OSError, ValueError, EOFError):
        print("FAIL 网络连接、输入文件或输入格式错误；请检查服务地址与请求文件")
    return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
