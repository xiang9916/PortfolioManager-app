# App 层三分：优化运行 / 行情同步 / 会话状态

> 2026-10-24。关联：ADR 0001（一键填入只用当下值）、0009（估值唯一入口）、0010（资产身份）。

## 背景

`AppStore` 是 App 层唯一的 @MainActor 状态中枢，但它同时承担了六件事：会话 UI 状态、
行情/股息/汇率/宏观同步、优化运行、备份导入导出、财务底稿防抖落盘、路径与 UserDefaults。
2026-10-24 的只读评审量到：

- 1043 行、37 个 public 存储属性、27 个 public 方法；10 个视图文件里对 `store.*` 的引用共 121 处、48 个不同成员。
- 两条 **85 行**的优化轮询循环（主运行 / 新标的测试）除「写哪个字段」外逐字相同。
- **第三条优化器入口在视图里**：`OptimizationResultView.runSensitivity()` 现场 new sidecar + Database +
  OptimizationService，并且因为 View 的 `Task` 继承 MainActor、`sidecar.run` 同步等子进程，
  一次敏感性分析（实测约 156s）期间 UI 完全不可响应；`refreshMacroRates()` 同样在每次启动阻塞主线程。
- extract 组装 88 行零 Swift 测试，而它是 App 与 Python 之间**唯一**的输入契约面。
- pm-cli 的 `refresh` 是第二套实现：不写 `quotes`（市值不变）、不抓股息（股息率不变），
  还写一条日期与金额都写死的 snapshot（自 a0ccbc0 起；2026-09-30 的隐私清理没扫到，因为清理正则只认千分位）。
- App 层没有 testTarget（`Package.swift` 的 testTarget 只依赖 `PortfolioCore`），上述流程只能人眼验证。

## 决策

拆出三个深 module，App 与 CLI 退化成 adapter：

| module | 位置 | 公开面 |
|---|---|---|
| `OptimizationRunner` + `LiveExtract` | PortfolioCore/Optimizer | `run(request, onEvent:)`、`runSensitivityOffMain(config:extractJSON:anchor:)`、`LiveExtract.apply(to:_:)` |
| `MarketDataSync` + `FxRateSync` + `MacroRateSync` + `BenchmarkSeries` | PortfolioCore/DataSources | `run(SyncPlan, into: Database) -> SyncReport` 等；计划与文案分离 |
| `AppStore`（会话状态 + 意图） | PortfolioManager | 只持快照与用户意图；`db / repository / optimizer` 收为 private |

注入面（全部是测试可替换的窄缝）：`OptimizationDriving`（假运行）、`DataSource`（已有）、
`SidecarConfig`（子进程参数）、`SyncPlan`（目标/时钟/并发）、`QuarterlyDesk.Persistence`（落盘）、
`AppEnvironment`（路径 / UserDefaults / 时钟）。

### 顺序：0 → A → B → C

原评审建议「先 MarketDataSync，再 OptimizationRun，最后 AppModel」。**改为先把 OptimizationRun 提到第一**：

1. 行情同步的重复是 130 行内联逻辑 + 一份 CLI 复制；而 OptimizationRun 射程内有两块更糟的：
   零测试的 Python 契约（extract）与视图里的第三套装配 + 阻塞主线程。重复的行数不是风险，未测试的契约面才是。
2. 第一刀必须在不碰网络的前提下验证「Core module + store 薄壳 + 假实现测试」这套模式；
   行情同步带网络/并发/SQLite 批量写/TTL，拿它当第一刀等于在最高风险处试探新模式。
3. 两者互不依赖（A 需要 extract + service；B 需要 sources + db），顺序不受依赖约束 →
   按「机械重复 → 并发 I/O → 视图面」的风险升序排。

## 有意改变的行为

- pm-cli `refresh` 现在也写 `quotes`、也抓股息（与 App 同一份实现），并删掉了写死的 snapshot。
- 敏感性与宏观利率改在**后台线程**执行（数值不变，UI 不再卡死）。
- 视图不再能直接拿到 `Database / OptimizationService / UserDefaults`。

## 保持不变的语义

- 所有用户可见文案（行情更新完成…、汇率更新完成…、季度已存在…）逐字不变。
- 行情同步的两条硬约束：网络阶段并发且不碰库；落库阶段单线程批量写（决策 Q31/Q33/Q34）。
- 股息 7 天缓存 + 同自然年重抓（Q17/Q24）；失败标的不中断同步。
- 优化的事件顺序（started → steps → finished）、收尾 `finalize()`、主运行结束后 `loadAll + fetchBenchmark`。
- 无 Schema 变更、无 JSON 契约变更、无 Python 改动、无 CLI 参数/退出码变更。

## 非目标

- 不把 App 提到库 target：`AppModel` 仍然只能靠 Core 化逻辑间接覆盖（`HoldingEditor` / `QuarterlyDesk` 已下沉）。
  库化会改 `Package.swift` 与全部 import，收益不抵风险，记为后续可选。
- 不启用 `PRAGMA foreign_keys`、不做资产 key 迁移（ADR 0010 的独立任务）。
- 不重写 git 历史以清除 a0ccbc0 里的金额。

## 落地状态（2026-10-24）

- 切片 0（红线清理 + ADR 0001 事实更正）、A、B、C1（`HoldingDraft`/`HoldingEditor`/`QuarterlyDesk` 下沉 Core）、
  C2（`AppEnvironment` + `targetReturn` 持久化收口）已落地。
- **C3 未做**：视图仍以 `store.xxx` 读取，尚未改成「每 Tab 一个 projection」。
  预期的下一步与验收口径：每个视图文件对 `store.*` 的引用 ≤3、4 个 Tab 截图无差异。

## 验证

- Swift：166（A 后）→ 201 用例全绿（新增 52；2 个联网用例按设计跳过）。
- 等价门：`LiveExtractTests.testMatchesLegacyDictionaryRewrite` 把改造前的字典改写作为 oracle，逐键比对。
- `pm-cli --self-test` PASS；`scripts/privacy_check.sh` 通过。
