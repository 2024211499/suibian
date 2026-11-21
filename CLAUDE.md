# 随变 仓库协作规则（Codex / Claude 通用，AGENTS.md 与 CLAUDE.md 内容相同）

配套文档 [随变技术执行方案](docs/随变_技术执行方案.md) 是实现依据，本文的功能编号、章节号都指向它。

## 规则

1. **一次只做一个模块。** 上下文只给：`schema/` 里相关的 JSON Schema、技术文档对应的那一节、要改的文件。不要把整个仓库丢给你（AI）。
2. **先写测试再写实现。** 先按技术文档 13.1 的表写测试，人确认测试符合文档后，再写实现直到测试通过。
3. **`schema/`、`actions.yaml`、`.env.example` 只能人改。** 你想改这些文件时，先说明理由，人确认后再动。
4. **不许在代码里写死模型名、API Key、城市名。** 模型名只从 `.env` 读。
5. **不许调用 12.3 列出的禁用接口**（SmsManager、AccessibilityService、支付 SDK、READ_CONTACTS、READ_SMS、SYSTEM_ALERT_WINDOW、ACCESS_BACKGROUND_LOCATION 等）。`scripts/forbidden_scan.sh` 不过就不能合并。
6. **界面上出现的每一个概率、时间、距离都来自引擎或高德返回值。** 不许让大模型生成数字；文案一律用模板 + 占位符（技术文档 5.4，`app/llm/render.py` 会拦截违规输出）。
7. **每次说"完成了"，必须贴出实际运行的测试命令和输出。** 没跑过的不算完成。

## 人自己要守的三条（AI 无需执行，但要提醒）

- 后台、闹钟、通知、权限相关代码，AI 写完后必须在真机验证（至少一台 ColorOS），模拟器不算数。
- AI 批量生成的回放样本，`source` 一律标 `synthetic`，报告里真实和合成分开统计。
- 每周日验收对照《随变_两人冲刺方案.md》第三节逐项勾。

## 常用命令

```bash
# 服务端
cd server && pytest                # 单元 + 性质测试
cd server && ruff check app        # 静态检查
bash scripts/forbidden_scan.sh     # 禁用接口扫描（Windows 用 Git Bash）

# Android
cd android && ./gradlew testDebugUnitTest assembleDebug
```

## 当前状态提示（随进度更新）

- 服务端默认 `MOCK=1`：所有接口返回 `schema/examples/` 示例。真实实现完成后在 `.env` 里改 `MOCK=0`。
- `engine/simulate.py`、`engine/profile.py` 是 v0 参考实现（对应技术文档 7.1/7.2/7.5），接画像和情境修正时按 TODO 标记改。
- Android 的 `ActionType.kt` 是手写占位，W1 的 Gradle 任务要从 `schema/actions.yaml` 自动生成（技术文档 12.1）。
