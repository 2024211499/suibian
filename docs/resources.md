# 外部资源与工具清单（随变）

> 2026-11-27 整理。star 数为当天 GitHub API 快照。用法标记：🟢 直接用 / 🔵 抄思路 / ⚪ 仅参考。
> 配套：《随变_两人冲刺方案.md》《随变_技术执行方案.md》。

---

## 〇 项目审核快照（2026-11-28 更新；W1 = 11/23–11/29）

| 维度 | 状态 |
|---|---|
| 测试 | ✅ pytest 40/40；ruff 全绿；forbidden_scan 6 项全过 |
| 数据契约 | ✅ schema 单源 + 9 个 mock 示例 + 契约链测试（生成→候选→推演→重排全程 Schema 校验） |
| 推演引擎 | ✅ 硬约束/拓扑/悬空边/上界已修，审计回归守着 |
| 动作关卡 | ✅ 白名单+四级确认+权限+幂等；**24 项 Android 单测全过 + APK 构建成功（11/28 实测）**；⚠️ 执行器实现类（CalendarWriterExecutor 等）尚未编写，甲7 前需补 |
| 部署 | ✅ 镜像带 schema、启动拒启、DOMAIN 注入、冒烟脚本（**未真跑 docker**） |
| 核心空缺 | W2 真解析/推演接线 → W3 方案搜索 → W4 生成流水线 → W5 复盘画像 |

**W1（11/23–29）周日验收清单**：`/v1/health` 全绿；手机显示 mock 计划把握；spike1 锁屏闹钟结论；高德月配额实查（搜索 5000 次/月那行）。后两项必须本周完成。

**重要发现（2026-11-27 搜索）**：GitHub 上 "trip planner" 类最高仅 372⭐（CLI 工具），其余 33–82⭐ 项目全是"LLM 直接生成行程"——**没有同构的"算把握 + 变化应变闭环"开源实现**。计划书新颖性论述可直接引用。

---

## 一、Android 端（甲）

| 仓库 | Stars | 用法 | 价值 |
|---|---|---|---|
| [android/nowinandroid](https://github.com/android/nowinandroid) | 22,141 | 🔵 | Google 官方 Compose+Hilt+Room+DataStore 全功能范本；模块化、测试组织照它补细节 |
| [Genymobile/scrcpy](https://github.com/Genymobile/scrcpy) | 155,920 | 🟢 | 演示视频录屏：USB 60fps 无水印；W7 录素材必备 |

**UI 库调研（2026-11-28，结论：不引大框架）**——本项目是效率型界面（卡片+列表+表单），Compose+Material3 已是最强选型，高 star 库解决的是我们没有的问题：

| 库 | Stars | 判断 |
|---|---|---|
| [PhilJay/MPAndroidChart](https://github.com/PhilJay/MPAndroidChart) | ~37k | ❌ 不用：W7 校准曲线在服务端 sklearn 出图，App 无图表需求 |
| [airbnb/lottie-android](https://github.com/airbnb/lottie-android) | ~35k | ❌ 不用：演示走 scrcpy 录屏，动画只增包体和叙事负担 |
| [Coil-kt/coil](https://github.com/Coil-kt/coil) | ~11k | 🔵 W5 截图导入需显示缩略图时引入（单一职责小库） |
| [android/compose-samples](https://github.com/android/compose-samples) | 官方 | 🔵 甲7/甲8 多页面（部署确认页/日志页）写法参考；导航用已有 hilt-navigation-compose 传递依赖，零新增 |

引入标准：某个具体任务用到时引那个单一职责的小库；"为了以后可能用"的一律不引（同 langchain 的判断逻辑）。

高德官方示例在 GitHub 组织 `amap-demo`（不在 star 榜）；导航 URI 调起我们已实现，无需第三方封装。

## 二、服务端 LLM 层（乙）

| 仓库 | Stars | 用法 | 价值 |
|---|---|---|---|
| [BerriAI/litellm](https://github.com/BerriAI/litellm) | 64,023 | 🔵 | 与 `llm/client.py` 同职责（路由/熔断/费用）。P0 用自研 50 行版；P1 参考其熔断参数或换用 |
| [567-labs/instructor](https://github.com/567-labs/instructor) | 14,148 | 🔵 | 结构化输出+校验重试；抄"错误信息回喂重试"的提示词模板 |
| [langfuse/langfuse](https://github.com/langfuse/langfuse) | 37,923 | 🟢(P1) | LLM 观测/评测平台（自部署）。"成功率<95% 改提示词"的运营看板 |
| [promptfoo/promptfoo](https://github.com/promptfoo/promptfoo) | 27,747 | 🔵 | 提示词回归测试框架，管理 `run_generate.py` 的测试集 |
| [songquanpeng/one-api](https://github.com/songquanpeng/one-api) | 37,485 | ⚪ | 自部署 LLM 网关（多 key/配额）；P0 规模不需要 |
| [pydantic/pydantic-ai](https://github.com/pydantic/pydantic-ai) | 21,427 | ⚪ | Pydantic 官方 agent 框架；我们引擎主导的设计用不上 |
| [langchain-ai/langchain](https://github.com/langchain-ai/langchain) | 150,082 | ⚠️ 别引入 | 重依赖会模糊"自研 vs 复用"叙事；只看思路 |

## 三、算法与评测

| 仓库 | Stars | 用法 | 价值 |
|---|---|---|---|
| [google/or-tools](https://github.com/google/or-tools) | 14,463 | 🔵+🟢(P1) | CP-SAT 解"最小放宽集"（比枚举 2⁸ 优雅、可扩展）；带时间窗排程可交叉验证束搜索——评测多一个"vs 商用求解器"对照 |
| [scikit-learn/scikit-learn](https://github.com/scikit-learn/scikit-learn) | 68,362 | 🟢 | W7 评测出图：`calibration_curve()` + `brier_score_loss()` |
| [opentripplanner/OpenTripPlanner](https://github.com/opentripplanner/OpenTripPlanner) | 2,786 | 🔵 | 多模式行程规划参考架构（GTFS 时刻表建模）；对照检查我们公交等车指数分布假设 |

## 四、本机 Agent Skills（写文档/查新/测试时用）

- **mermaid-diagram** —— 计划书/答辩的架构图、8.2 状态机图、四阶段时序图
- **arxiv / prior-art-search** —— pSTN（probabilistic Simple Temporal Network）、机会约束调度的学术出处（答辩"技术深度"页引用）
- **novelty-check** —— 初赛前系统性查新
- **web-gui-tester** —— P1 H5 同行人页黑盒测试

## 五、落地节奏

| 时间 | 动作 |
|---|---|
| 本周（W1） | 装 scrcpy；git 推远端让 CI 真跑；clone nowinandroid 当编码对照 |
| W3–W4 | `search()` 实现时读 or-tools CP-SAT 文档；提示词稳定后接 promptfoo |
| W7 | sklearn 出校准曲线 |
| 区域赛前 | langfuse 接入评估；or-tools 交叉验证；litellm 是否替换自研 client |

## 六、本机环境安装记录（2026-11-27 实测，11/28 补 Android 构建验证）

## 七、配额参数（乙1 实查控制台后更新本节数字）

> 高德个人认证开发者月配额（2025-05-20 起新政，2026-11-27 经官方页面核实）：

| 服务 | 月配额 | 随变用途 | 日限额（服务端强制） |
|---|---|---|---|
| **基础搜索**（`/v5/place/text`、`/v5/place/around`、`/v5/place/detail`、输入提示） | **5,000 次/月** ← 最稀缺 | W4 生成流水线找 POI；`swap_place` 换同类地点 | `DAILY_SEARCH_MAX=120` |
| 基础 LBS（geocode、路径规划、距离测量） | 15 万次/月 | parse 地理编码（乙4）、监控刷新、推演路段 | 无单独上限（月耗估算 ~5.4 万） |
| 天气预报 | 5,000 次/月（和风另有免费订阅 ~5 万次/月） | refresh/tick 天气感知 | — |

- 搜索流量包：30 元/万次（不参与阶梯折扣），W4 开生成前按需购 1–2 个兜底
- `DAILY_GENERATE_MAX=10`（每次生成 ≈ 搜索类 9 次 + 距离 8–10 次 + 路径 ≤15 次）
- `PARSE_LLM_DAILY_MAX=200`（乙6 落地）
- ⚠️ 以上为平台公开口径，**以 W1 控制台实查为准**（乙1 截图存档于 `docs/`，实查后覆盖本表数字）

### 装好的东西

| 项 | 结果 | 说明 |
|---|---|---|
| **scrcpy** | ✅ winget 装好 | 新开终端生效（`scrcpy` 命令）；录屏演示素材用，`scrcpy --record demo.mp4` |
| **Android 构建链路** | ✅ **全通** | 11/28 复核：testDebugUnitTest **53/53 通过** + assembleDebug 出 `app-debug.apk`（约 55 MB，含 ML Kit 中文 OCR 模型） |
| Python venv | ✅ | `suibian/.venv`（3.13），服务端依赖齐 |
| Android SDK | ✅ 已有 | `D:/Android/Sdk`（platforms 到 android-35，build-tools 35.0.0，licenses 已接受） |
| Git 仓库 | ✅ | 已有 GitHub 私有远端，Android 开发走 `feature/android-w1w2` 和草稿 PR #1 |

### 本机构建的四个坑（都已解决，照抄即可）

1. **JAVA_HOME 指向 JDK 8**（全局环境变量问题）。构建时显式指定：
   `-Dorg.gradle.java.home=C:/Program Files/Java/jdk-21`
2. **services.gradle.org 被墙**（wrapper 下载发行版超时）；腾讯镜像返回的 zip 是坏的。本机构建**直接用已缓存的完整发行版**（见下方命令），`gradle-wrapper.properties` 保持官方 URL 供 CI 用。
3. **local.properties 转义坑**：必须写 `sdk.dir=D:/Android/Sdk`（正斜杠），反斜杠转义会被 properties 语法解析成坏路径。
4. **中文路径**（`D:\软创\随变\...`）：AGP 路径检查 + 深层工具链 IOException。已建目录联接（免管理员）：
   `mklink /J C:\suibian D:\软创\随变\suibian` —— **构建一律走 `C:\suibian\android`**。

### 本机标准构建命令（复制即用）

```bash
cd /c/suibian/android
GRADLE="/c/Users/lenovo/.gradle/wrapper/dists/gradle-8.10.2-all/ezxzryhqdbf6stsda8vkgso9x/gradle-8.10.2/bin/gradle.bat"
"$GRADLE" testDebugUnitTest --no-daemon "-Dorg.gradle.java.home=C:/Program Files/Java/jdk-21"   # 单测
"$GRADLE" assembleDebug   --no-daemon "-Dorg.gradle.java.home=C:/Program Files/Java/jdk-21"   # APK → app/build/outputs/apk/debug/
```

Android Studio 打开项目时也建议打开 `C:\suibian\android`（联接路径），避开中文路径问题。

### 还没装的（按落地节奏）

- promptfoo / langfuse / or-tools：W3+ 或区域赛前再装（见第五节节奏表）
- ✅ scikit-learn 1.9.1 已装进 .venv（校准曲线：`sklearn.calibration.calibration_curve` + `sklearn.metrics.brier_score_loss`）

## 八、spike1 锁屏闹钟实测结论（2026-11-28，甲3）

**设备**：vivo V2359A（OriginOS，Android 16 / API 36）。⚠️ 任务书原定 OPPO/ColorOS，本机现为 vivo；ColorOS 验收项待补测。

**方法**：HomeScreen 调试按钮（30 分钟后出门闹钟 / 立即 tick）+ `adb shell dumpsys alarm / notification` 取系统级证据。

**结论：setAlarmClock 准点达标，无需降级 setWindow（10.3 预案备用不启用）**
- 三次触发全部准点：计划 09:14:25.283 / 09:22:51.542 / 09:27:31.146 → 系统派发误差均 < 1 秒（alarm stats `3 wakes 3 alarms` 与计划一一对应）
- `window=0`，`exactAllowReason=policy_permission`（USE_EXACT_ALARM 生效），注册为系统 "Next wake from idle"
- 重装 APK 触发 `MY_PACKAGE_REPLACED → BootReceiver.rebuild()` 后闹钟自动恢复并准点触发——持久化恢复路径实测有效

**踩坑记录（W2 tick 提醒 / 正式演示都会再用到）**：
1. **vivo 智能分类静默拦截通知**：无 category 的通知被记 `intercepted: …new:!priority`（横幅声音全无、通知栏不显示）。修复：`NotificationHelper.notifyDepart` 加 `setCategory(CATEGORY_ALARM)` 后放行（勿删）。
2. **POST_NOTIFICATIONS 必须运行时授予**（Android 13+）：pm grant 或首页按钮内请求均可，重装不丢。
3. **通知音量按输出通道独立**：扬声器通道音量为 0 时有横幅无铃声（`dumpsys audio` 里 STREAM_NOTIFICATION speaker=0）；演示前检查手机通知音量。
4. **tick 在 W1 无可见反馈**（联网判定 W2 才接）：已在 AlarmReceiver 给 ACTION_TICK 加调试 Toast。
5. setAlarmClock 的闹钟**不出现在 vivo 时钟 app 列表**（第三方 API），以状态栏闹钟图标 + dumpsys 为准。
6. 真机 USB 连接不稳定（充电线/接口），掉线后重启 adb server + 换线重插；自动点按钮需开「USB调试（安全设置）」。
7. **系统日历提醒**：日历事件与 `CalendarContract.Reminders` 行成批写入，按计划 `calendar_reminder_minutes` 提前提醒，缺省 5 分钟，0 表示开始时提醒。撤销时核对日历事件和提醒的分钟数、方式及条数。真机可能保留 `DELETED=1` 的软删除行，验收查询只统计有效事件。

**严格版补测（同日 10:05）**：30 分钟闹钟（计划 10:05:51.780）准点触发，误差约 +0.2 秒（alarm stats 单次 wake 与计划时刻对应）。

**遗留**：甲2 的 2 小时/过夜实测与 ColorOS 机型验收待跑；横幅/铃声的肉眼最终确认需真人复核（系统层面已放行无拦截记录）。
