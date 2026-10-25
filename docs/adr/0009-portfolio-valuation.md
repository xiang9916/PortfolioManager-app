# ADR 0009 — 组合估值的单一入口与最新价口径

- 状态：已采纳（2026-10-24）
- 范围：`Sources/PortfolioCore/Query/PortfolioValuation.swift`、`Repository`、`AppStore`、资产总览；Schema 不变。
- 关联：ADR 0005（总回报口径）、ADR 0008（真实现价 raw_close）、ADR 0002（池由市场派生）。

## 背景

「组合此刻值多少」没有单一 module：`Repository` 的 6 个 fetch 各自重建 context、各自算市值 / 折汇 / 权重；池比例在 `AppStore` 与 extract 里各算一次；折汇 1:1 兜底在 6 处手写。具体后果：

- 缺 `quotes` 时最新价回落 `prices.last.close`；基金的 `close` 是**累计净值**、`rawClose` 才是单位净值（ADR 0005 / 0008）。`pm-cli refresh` 只写 prices 不写 quotes，因此这条路径可达，市值可能差几倍。
- `latestPrice` 取 quote、`latestDate` 取 prices 末行，二者不是同一时点。
- `AllocationSnapshot.asOfDate` 取 `holdings.first`（按 asset_key 排序后的第一只），不是组合估值日。
- 缺汇率按 1:1 折算且无任何标记。

## 决策

1. **单一 module**：`PortfolioValuation.make(holdings:assetsByKey:quotes:lastPrices:fxRates:)` 是纯函数；`Repository.loadValuation()` 是唯一 loader；六个 `fetch*` 只做投影（可接收已加载的估值）。`PortfolioContext` / `loadContext` 删除。
2. **最新价顺序**：`quote → rawClose → close（告警）→ missing（0，告警）`。
   - `rawClose` 对权益是未复权收盘、对基金是单位净值，都是现实价格；
   - `close` 只在没有真实现价时兜底，并产生 `totalReturnBasis` 告警；
   - 无任何价格时 `priceLocal = nil`、市值按 0，并产生 `missingPrice`。
3. **缺汇率 / 非法汇率**：按 1:1 折算（数值语义与今天一致），产生 `missingFX(currency)`（每币种一次）。**币种不一致**（行情快照 / K 线的币种 ≠ 持仓币种）时，折算基准仍是**持仓币种**（与旧 `fx[h.currency]` 一致），并产生 `currencyMismatch` 告警——不替用户猜币种。
4. **时间语义**：`asOfDate = max(priceDate)`；`latestPrice` 与 `latestDate` 同源（quote 价配 quote 日期）。
5. **权重**：`weight = valueCny / totalValueCny`（total > 0）；子集权重走 `PortfolioValuation.weights(for:)`，删除 App 层两处内联除法。
6. **AllocationSlice.pool**：取组内市值绝对值最大的持仓（今天无读取者，只修语义）。
7. **告警可见**：资产总览在卡片下方展示前两条 + 计数，完整列表在 tooltip；Core 的 `ValuationWarning.message` 是唯一文案来源。

## 被否方案

- **用 `close` / `rawClose` 比例 rebase 近似真实现价**：基金累计净值与单位净值不是同一个量，rebase 后仍是错的；权益的分红窗口也会引入偏差。
- **要求必须有 quote 才估值**：断网或只跑过 `pm-cli refresh` 时总市值会变成 0，比“用真实现价估算并标注”更糟。
- **缺汇率按 0 计**：会静默丢钱；保持 1:1 + 告警。
- **保留 `PortfolioContext` 作为缓存**：它只是四个 map + 一个总数，仍然让每个调用者自己算派生量；不是 interface。

## 后果

- **无 quote 的基金总市值会下降**（累计净值 → 单位净值），这是修复；权益从复权收盘 → 未复权收盘，同样更接近现实价格。
- `rawClose` 未回补（v10 前历史、未刷新）时会出现 `totalReturnBasis` 告警；刷新后自动消失。这是诚实降级，不编造价格。
- `asOfDate` 语义从「持仓记录日」变为「最晚价格日」；当前 UI 未读取该字段。
- 无 Schema / CLI 参数 / 结果 JSON 键变化；`pm-cli overview` 的数值仅在兜底场景变化。
- 无任何可用价格时总市值为 0、池占比回退 50/50（优化器 extract 的“无数据”兜底，与旧 `livePoolWeights` 一致）；持仓的池归属仍按市场派生，只是没有权重可算。
- 每次 `loadAll()` 只装配一次估值；performance / focus 仍各自读价格历史（跨 fetch 的缓存留给下一层）。

## 验证入口

- `Tests/PortfolioCoreTests/PortfolioValuationTests.swift`（14 项，纯 fixture）
- `Tests/PortfolioCoreTests/RepositoryValuationTests.swift`（4 项，临时库）
- 全量 `swift test --disable-sandbox`：131 用例 / 2 联网跳过 / 0 失败。
