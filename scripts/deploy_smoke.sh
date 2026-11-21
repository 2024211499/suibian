#!/usr/bin/env bash
# 部署冒烟测试 —— 审计 P1-4 的验收：健康检查 + 一个实际读 schema 的业务接口都要过。
# 用法: bash scripts/deploy_smoke.sh https://your-domain
# /v1/parse 必须成功并返回草案，正式部署验收也使用同一条件。

set -u
BASE="${1:-http://127.0.0.1:8000}"
FAIL=0
BODY=$(mktemp) || exit 1
trap 'rm -f -- "$BODY"' EXIT

echo "== 部署冒烟: $BASE =="

code=$(curl --silent --show-error --max-time 15 -o "$BODY" -w "%{http_code}" "$BASE/v1/health")
if [ "$code" = "200" ] && grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"' "$BODY"; then
    echo "PASS /v1/health -> 200"
else
    echo "FAIL /v1/health -> $code"
    FAIL=1
fi

: > "$BODY"
code=$(curl --silent --show-error --max-time 30 -o "$BODY" -w "%{http_code}" -X POST "$BASE/v1/parse" \
    -H 'Content-Type: application/json' \
    -d '{"text":"\u660e\u65e98\u70b9\u4e09\u6559\u4e0a\u8bfe","now":"2026-12-04T21:00:00+08:00"}')
if [ "$code" = "200" ] && grep -Eq '"draft_plan"[[:space:]]*:[[:space:]]*[{]' "$BODY"; then
    echo "PASS /v1/parse -> 200"
else
    echo "FAIL /v1/parse -> $code（预期 200 和计划草案）"
    FAIL=1
fi

if [ "$FAIL" -eq 0 ]; then echo "冒烟通过 ✓"; exit 0; else echo "冒烟失败 ✗"; exit 1; fi
