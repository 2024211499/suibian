# 随变（suibian）

> 一句话出计划，变了也不慌。第二十届全国大学生软件创新大赛 · 赛题2。
> [竞赛方案](docs/随变_竞赛方案_v2.md) · [技术执行方案](docs/随变_技术执行方案.md) · [两人冲刺方案](docs/随变_两人冲刺方案.md) · [初赛 Demo 规格](docs/随变_初赛Demo实施规格.md) · [开发与验收手册](docs/随变_从零开发与逐步验收手册.md)

## 目录结构（每处要填什么）

```
suibian/
├─ schema/                  ← ★ 第一周先定稿，之后改动需两人同意
│  ├─ plan_graph.schema.json     计划图（技术文档 2.1）
│  ├─ intent.schema.json         一句话生成的意图输出（2.1 生成意图）
│  ├─ skeleton.schema.json       活动骨架输出（2.1 生成骨架）
│  ├─ actions.yaml               动作分级表，唯一来源（12.1）
│  ├─ context_rules.yaml         情境修正规则（6.2）
│  ├─ testvectors/profile.json   画像更新测试向量，Python/Kotlin 共用（7.5）
│  └─ examples/                  固定候选、解析示例和接口样例
├─ server/                  ← 乙负责
│  ├─ app/api/        REST + SSE 接口（第三节）
│  ├─ app/llm/        大模型客户端、校验、脱敏、模板渲染（第五节）
│  ├─ app/engine/     推演 / 方案搜索 / 画像 / 情境修正（第七节）★ 自研核心
│  ├─ app/generator/  一句话生成流水线（第四节）
│  ├─ app/gateway/    高德 / 和风适配器，缓存、限流（第六节）
│  ├─ app/monitor/    tick 感知判定（8.3）
│  └─ tests/          单测 + 性质测试（13.1–13.2）
├─ android/                 ← 甲负责
│  └─ app/src/main/java/com/suibian/app/
│     ├─ ui/          Compose 页面（10.2 页面清单）
│     ├─ domain/      PlanStateMachine（8.2）、BayesUpdater（7.5）★ 纯 Kotlin 可单测
│     ├─ data/        Room、Retrofit、SSE 客户端
│     ├─ executor/    ActionType + ActionGateway（第十二节）★ 自研核心
│     ├─ background/  闹钟调度（10.3）、接收器
│     └─ di/          Hilt 模块
├─ eval/                    ← 回放评测 / 生成评测 / 对照组（13.3–13.4）
├─ scripts/forbidden_scan.sh  禁用接口扫描（12.3），CI 必过
├─ deploy/                  Docker Compose + Caddy + .env.example（第十四节）
└─ h5/                      同行人页面（P1，技术文档第十一节）
```

★ = 两人必须亲自写/看懂的代码（AI 只起草）。

## 快速开始

### 服务端本地联调

```powershell
Set-Location server
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
$env:MOCK = '1'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

在另一终端查询 `/v1/health`，确认 `status=ok`、`checks.schema=ok`。默认启用登录鉴权，账号初始化及客户端接入见[账号与团队凭据联调](docs/乙_账号与团队凭据联调.md)。

从仓库根执行 `bash scripts/deploy_smoke.sh http://127.0.0.1:8000 --allow-mock --login`，检查开发模式下的资源打包和真实推演。真实服务使用 `bash scripts/deploy_smoke.sh https://你的服务地址 --login --parse-file request.json`，其中 `request.json` 是包含明确起终点、交通方式及带时区 `now` 的完整解析请求。已有令牌可通过 `SUIBIAN_SMOKE_TOKEN` 环境变量传入；不要将密码或令牌写进命令参数。任一检查失败返回非零退出码，开发模式通过与真实录入通过分别报告。

当前资源、存储位置和待核验额度见[乙方资源记录](docs/resources.md)。

### Android

1. 使用 JDK 17、Android SDK 35，打开 `android/`。项目自带 Gradle Wrapper。
2. Windows 下把工作副本放在纯英文路径（例如 `D:\dev\suibian`），打开其中的 `android/`，运行 `.\gradlew.bat :app:testDebugUnitTest :app:assembleDebug`。当前中文路径下即使跳过 AGP 的路径检查，测试运行器也出现了 `ClassNotFoundException`。在 Android Studio 的 Gradle 设置中选择 JDK 17 或 21。
3. Android 首页用于后续接入联调。模拟器服务地址为 `10.0.2.2:8000`，真机使用可访问的服务地址，Retrofit 配置位于 `di/AppModule.kt`。

### 开发基线

1. `MOCK=1` 下 POST `/v1/generate` 提交固定示例任务。用 `job_id` 轮询或订阅 SSE，两种方式拿到同一批候选。
2. 把选中候选的完整 `plan` 与 `depart_min` 传给 POST `/v1/simulate`。两种运行模式均调用真实推演。首版行程采用单链，出发时刻用计划当天的分钟数。
3. POST `/v1/replan` 额外接收 `{"trigger":{"kind":"weather","data":{"edge_id":"e1","delay_min":15}}}`。服务端使用传入的预计延误复算，返回受影响节点和符合目标把握的同计划新版。换路线与自动天气感知仍在后续开发范围。
4. 下一批共同验收是 Android 端选候选、保存版本和调用推演。完整在线生成、方案搜索、日历执行和目标机闹钟测试均待接入。

首轮联调以 `schema/` 和 `server/tests/test_contract.py` 为准。初赛 Demo 规格中的 `/healthz`、`revision`、`legs`、`p_hard` 在当前实现分别对应 `/v1/health`、`version`、`edges`、`sim.p_all`。`/v1/generate` 当前先返回 `job_id`，Android 首轮取候选走轮询。规格中的 `/v1/check-context` 和带行程进度的重排属于后续实现，两端一起确认输入、输出样例后再改契约。

## 协作规矩

见 `AGENTS.md` / `CLAUDE.md`（内容相同）：一次一个模块、先写测试、schema 只能人改、禁用接口扫描不过不合并、说"完成"必须贴测试输出。

两人从最新的 `main` 各开功能分支，推送到同一个仓库后向 `main` 提交 PR。由另一人检查变更、复跑验收，再合并。PR 说明列出实测命令、结果和仍待真机验证的内容。

## 第一周（W1）对照

W1 逐日清单见《随变_两人冲刺方案.md》第九节。本仓库骨架已包含：schema 四份草案、mock 模式、spike1 所需的闹钟工具类（`AlarmScheduler`）、CI 三个 workflow、`forbidden_scan.sh`。
