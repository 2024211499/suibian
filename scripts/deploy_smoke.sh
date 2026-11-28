#!/usr/bin/env bash
# 用法：bash scripts/deploy_smoke.sh BASE [--allow-mock] [--login] [--parse-file request.json]
# 令牌仅从 SUIBIAN_SMOKE_TOKEN 读取；脚本不自动创建账号。
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -x "$ROOT/.venv/Scripts/python.exe" ]]; then
    PYTHON="$ROOT/.venv/Scripts/python.exe"
elif [[ -x "$ROOT/.venv/bin/python" ]]; then
    PYTHON="$ROOT/.venv/bin/python"
else
    PYTHON=python3
fi
exec "$PYTHON" "$ROOT/scripts/deploy_smoke.py" "$@"
