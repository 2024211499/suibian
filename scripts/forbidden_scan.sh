#!/usr/bin/env bash
# 禁用接口扫描 —— 技术文档 12.3。"代码里不存在的能力"靠 CI 保证，不靠自觉。
# 命中任意一条构建失败；本脚本与结果截图放进计划书，评委可以自己跑。
# 用法: bash scripts/forbidden_scan.sh   （仓库根目录或任意位置均可，Git Bash / Linux / macOS）

set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
FAIL=0

scan_src() {
    # $1=说明 $2=grep 正则 $3=目标目录 $4=include 模式
    local label="$1" pattern="$2" target="$REPO/$3" inc="$4"
    if grep -rEl "$pattern" "$target" --include="$inc" >/dev/null 2>&1; then
        echo "FAIL [$label] 命中: $pattern （目录 $3）"
        grep -rEn "$pattern" "$target" --include="$inc" | head -5
        FAIL=1
    else
        echo "PASS [$label]"
    fi
}

echo "== 禁用接口扫描（技术文档 12.3）=="

# 1. 不自动发短信
scan_src "不自动发短信"     "SmsManager|sendTextMessage|sendMultipartTextMessage" "android/app/src" "*.kt"
# 2. 不模拟点击、不替用户在别的 App 里操作（不用无障碍服务）
scan_src "不用无障碍服务"   "AccessibilityService|performGlobalAction|dispatchGesture" "android/app/src" "*.kt"
# 3. 不接支付 SDK（微信只用 getLaunchIntentForPackage 打开，不接微信开放平台 SDK）
scan_src "不接支付 SDK"     "com\.alipay|unionpay|com\.tencent\.mm\.opensdk" "android" "*.gradle.kts"
# 4. Manifest 与 10.5 的不申请清单一致
scan_src "不申请敏感权限"   "READ_CONTACTS|READ_SMS|SEND_SMS|SYSTEM_ALERT_WINDOW|ACCESS_BACKGROUND_LOCATION|BIND_ACCESSIBILITY_SERVICE" "android/app/src/main/AndroidManifest.xml" "*.xml"
# 5. 不上传聊天全文和通知原文（服务端不出现原始字段）
scan_src "不上传聊天原文"   "chat_text|notification_raw" "server/app" "*.py"

# 6. Kotlin 块注释可嵌套：注释内部出现"斜杠星号"序列会吞掉后续代码（审计 P1-1 实锤的坑）
python - <<'PYEOF'
import re, pathlib, sys
root = pathlib.Path("android/app/src")
bad = 0
for p in root.rglob("*.kt"):
    depth = 0
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        for tok in re.findall(r"/\*|\*/", line):
            if tok == "/*":
                depth += 1
                if depth > 1:
                    print(f"FAIL [Kotlin嵌套注释] {p}:{i}"); bad += 1
            else:
                depth -= 1
sys.exit(1 if bad else 0)
PYEOF
if [ $? -eq 0 ]; then echo "PASS [Kotlin嵌套注释]"; else FAIL=1; fi

echo "== 扫描目标: $(find "$REPO/android/app/src" "$REPO/server/app" -type f 2>/dev/null | wc -l) 个文件 =="

if [ "$FAIL" -eq 0 ]; then
    echo "全部通过 ✓"
    exit 0
else
    echo "存在违禁项，构建失败 ✗"
    exit 1
fi
