import Foundation

// MARK: - UI view models (shared across SwiftUI views)

/// One slice of the asset-allocation pie (grouped by asset class).
public struct AllocationSlice: Codable, Hashable, Identifiable {
    public let assetClass: String
    public let value: Double
    public let weight: Double
    public let pool: Pool
    public var id: String { assetClass }
}

/// Current portfolio allocation snapshot.
/// 境内/境外按每个标的的**市场**派生（`AssetMarket` → 池，见 Q26=C / Q31），
/// 与资产明细里显示的「池」一致；不再按资产大类分组取整组归属。
public struct AllocationSnapshot: Codable, Hashable {
    public let asOfDate: String?
    public let totalValue: Double
    public let domesticValue: Double
    public let overseasValue: Double
    /// 总成本 = Σ 各持仓「成本（折人民币）」；总市值 = totalValue。两者同口径（都含全部持仓）。
    public let totalCost: Double
    public let domesticCost: Double
    public let overseasCost: Double
    public let slices: [AllocationSlice]

    public init(asOfDate: String?, totalValue: Double, domesticValue: Double,
                overseasValue: Double, totalCost: Double = 0, domesticCost: Double = 0,
                overseasCost: Double = 0, slices: [AllocationSlice]) {
        self.asOfDate = asOfDate
        self.totalValue = totalValue
        self.domesticValue = domesticValue
        self.overseasValue = overseasValue
        self.totalCost = totalCost
        self.domesticCost = domesticCost
        self.overseasCost = overseasCost
        self.slices = slices
    }
}

/// One point of the reconstructed portfolio NAV (weighted index).
public struct PerformancePoint: Codable, Hashable, Identifiable {
    public let date: String
    public let value: Double
    public var id: String { date }

    public init(date: String, value: Double) {
        self.date = date
        self.value = value
    }
}

/// Summary statistics of historical performance (能力1 历史财务表现).
public struct PerformanceSummary: Codable, Hashable {
    public let startDate: String?
    public let endDate: String?
    public let totalReturn: Double
    public let annualizedVolatility: Double
    public let maxDrawdown: Double
    public let pointCount: Int
}

/// Per-asset row for the asset-perspective view (模块2).
public struct AssetPerspectiveRow: Codable, Hashable, Identifiable {
    public let assetKey: String
    public let name: String
    public let assetClass: String
    /// 解析后的市场（闭集）。nil = 未设置且 ticker 推断不出。
    public let market: AssetMarket?
    public let pool: Pool
    /// Holding currency (the currency `costBasis` is entered in; the derived value shares it).
    public let currency: String
    /// 市值(标的币种) = 份额 × 最后价 (派生, 不手填).
    public let value: Double
    /// 市值折人民币 (value × FX rate) — 权重计算基准.
    public let valueCny: Double
    /// 成本（折人民币）(costBasis × FX rate).
    public let costCny: Double
    /// 未实现资本利得 = 市值 − 成本 (人民币).
    public let unrealizedPnl: Double
    /// 收益率 = 未实现资本利得 / 成本 (成本为 0 时记 0).
    public let returnRate: Double
    public let quantity: Double
    /// 手工录入的持仓成本（原币, 与 `currency` 同币种）。
    public let costBasis: Double
    public let weight: Double
    public let latestPrice: Double?
    public let latestDate: String?
    /// 手动排序序号 (资产明细拖动排序).
    public let sortOrder: Double
    /// 股息派生块 (前年/去年/今年预计/今年已发生 + 来源与抓取时间).
    /// nil = 该标的既无资产行也无持仓 (理论上不出现)。
    public let dividend: AssetDividend?
    public var id: String { assetKey }
}

/// 能力4 财务分析: 个人资产/收益结构底稿 (组合级).
public struct FinancialAnalysis: Codable, Hashable {
    // 资产结构 (恒等式: 市值 = 原始本金 + 实盈实亏 + 未实现资本利得, 其中 本金 = 原始本金 + 实盈实亏)
    public let originalPrincipal: Double // 原始本金 = Σ 成本×汇率
    public let realizedPnl: Double       // 实盈实亏 = 累计股息分红 + 累计交易损益
    public let principal: Double         // 本金 = 原始本金 + 实盈实亏
    public let marketValue: Double       // 市值 = Σ 市值折¥
    public let unrealizedPnl: Double     // 未实现资本利得 = 市值 - 本金
    public let returnRate: Double        // 收益率 = 未实现资本利得 / 本金
    // 收益结构 (从 income_periods 累计)
    public let totalDividends: Double   // 累计股息分红
    public let totalRealizedPnl: Double // 累计交易损益
    public let totalIncome: Double      // 合计收益 = 实盈实亏 + 未实现资本利得 = 市值 - 原始本金
    public let totalReturnRate: Double  // 合计收益率 = 合计收益 / 原始本金
    public let periods: [IncomeSummary] // 期间明细
}

/// Shared context for portfolio queries: fetches holdings/fx/latest once,
/// reused across allocation/perspective/financial queries to avoid triple-fetching.
public struct PortfolioContext {
    public let holdings: [Holding]
    public let byKey: [String: Asset]
    public let fx: [String: Double]
    public let latest: [String: Double]
    public let totalValueCny: Double
}

/// High-level query layer: composes raw Database rows into UI-ready structures.
/// All methods are synchronous; call from a single (main-actor) context.
public final class Repository {
    public let db: Database

    /// Shared UTC "yyyy-MM-dd" formatter — creating one per fetchPerformance call is wasteful.
    private static let utcDayFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.timeZone = TimeZone(identifier: "UTC")
        return f
    }()

    public init(db: Database) {
        self.db = db
    }

    // MARK: FX + 派生市值 — 能力2 权重统一成人民币

    /// Load currency→CNY rates; CNY is always 1.0.
    private func fxRates() -> [String: Double] {
        var map = Dictionary(uniqueKeysWithValues: (try? db.fetchFxRates())?.map { ($0.currency, $0.rateToCny) } ?? [])
        map["CNY"] = 1.0
        return map
    }

    /// Latest price per asset key.
    /// Prefers the quotes table (unit NAV for funds, latest price for others);
    /// falls back to the last price-history point if no quote is stored.
    /// This ensures funds use 单位净值 for market value, while 累计净值 history
    /// is used only for the 模拟历史财务表现 chart.
    private func latestPriceMap(_ holdings: [Holding]) -> [String: Double] {
        let quotes = (try? db.fetchLatestQuotes()) ?? [:]
        var map: [String: Double] = [:]
        for h in holdings {
            if let q = quotes[h.assetKey] {
                map[h.assetKey] = q.price
            } else if let pts = try? db.fetchPrices(assetKey: h.assetKey), let last = pts.last {
                map[h.assetKey] = last.close
            }
        }
        return map
    }

    /// A holding's derived market value in its own currency: 份额 × 最后价.
    private func derivedValue(_ h: Holding, latest: [String: Double]) -> Double {
        h.quantity * (latest[h.assetKey] ?? 0)
    }

    /// A holding's derived market value converted to CNY (份额 × 最后价 × 汇率).
    private func valueCny(_ h: Holding, fx: [String: Double], latest: [String: Double]) -> Double {
        derivedValue(h, latest: latest) * (fx[h.currency] ?? 1.0)
    }

    /// Build a shared context (holdings + assets + fx + latest prices) in a single pass.
    /// All downstream fetch* methods accept this to avoid re-querying the DB.
    public func loadContext() throws -> PortfolioContext {
        let assets = try db.fetchAssets()
        let byKey = Dictionary(uniqueKeysWithValues: assets.map { ($0.key, $0) })
        let holdings = try db.fetchHoldings()
        let fx = fxRates()
        let latest = latestPriceMap(holdings)
        let total = holdings.reduce(0.0) { $0 + valueCny($1, fx: fx, latest: latest) }
        return PortfolioContext(holdings: holdings, byKey: byKey, fx: fx, latest: latest, totalValueCny: total)
    }

    /// Convenience: valueCny from context (avoids passing fx/latest separately).
    private func valueCny(_ h: Holding, ctx: PortfolioContext) -> Double {
        derivedValue(h, latest: ctx.latest) * (ctx.fx[h.currency] ?? 1.0)
    }

    // MARK: 市场 / 池 — 唯一解析入口 (Q26=C, Q31)

    /// 标的的市场（闭集）: 已填 market → ticker 推断 → nil。
    private func market(_ a: Asset) -> AssetMarket? {
        AssetMarket.resolve(market: a.market?.rawValue, ticker: a.ticker ?? a.key)
    }

    /// 标的的池归属: 由市场派生，市场解析不出来时按币种回退。
    /// **不再读 `assets.pool` 列** —— 那一列只是写入时的缓存，读时派生才能保证
    /// 池与股息税率永远从同一个市场结论出发。
    private func pool(_ a: Asset) -> Pool {
        AssetMarket.pool(market: a.market?.rawValue, ticker: a.ticker ?? a.key, currency: a.currency)
    }

    // MARK: 模块1 — asset allocation

    public func fetchAllocation() throws -> AllocationSnapshot {
        let ctx = try loadContext()
        let total = ctx.totalValueCny
        var groups: [String: (value: Double, pool: Pool)] = [:]
        // 池统计按「每个标的自身的市场」累计 — 修复: 之前按大类分组且组池被同组
        // 最后一个标的覆盖, 导致同一大类里的境内标的被计入境外池.
        var domestic = 0.0
        var overseas = 0.0
        var totalCost = 0.0
        var domesticCost = 0.0
        var overseasCost = 0.0
        for h in ctx.holdings {
            let a = ctx.byKey[h.assetKey]
            let cls = a?.assetClass ?? "其他"
            let p = a.map { pool($0) } ?? .overseas
            let v = valueCny(h, ctx: ctx)
            let c = h.costBasis * (ctx.fx[h.currency] ?? 1.0)
            if let cur = groups[cls] {
                groups[cls] = (cur.value + v, cur.pool)
            } else {
                groups[cls] = (v, p)
            }
            totalCost += c
            switch p {
            case .domestic: domestic += v; domesticCost += c
            case .overseas: overseas += v; overseasCost += c
            }
        }
        let slices = groups.map { (cls, v) in
            AllocationSlice(assetClass: cls, value: v.value,
                            weight: total > 0 ? v.value / total : 0, pool: v.pool)
        }.sorted { $0.value > $1.value }

        return AllocationSnapshot(
            asOfDate: ctx.holdings.first?.asOfDate,
            totalValue: total,
            domesticValue: domestic,
            overseasValue: overseas,
            totalCost: totalCost,
            domesticCost: domesticCost,
            overseasCost: overseasCost,
            slices: slices)
    }

    // MARK: 能力1 — historical performance (weighted NAV reconstruction)

    /// Reconstructs a trailing weighted-NAV series from current holdings + price history.
    /// Each asset is rebased to 1.0 at its first observation inside a trailing window
    /// (default 3 years) and weighted by its current derived CNY value. Cross-currency is ignored
    /// (FX is not embedded), so the series reflects relative weighted performance.
    public func fetchPerformance(lookbackYears: Double = 3.0) throws -> (points: [PerformancePoint], summary: PerformanceSummary) {
        let ctx = try loadContext()
        let holdings = ctx.holdings
        let total = ctx.totalValueCny
        guard total > 0, !holdings.isEmpty else {
            return ([], PerformanceSummary(startDate: nil, endDate: nil, totalReturn: 0,
                                           annualizedVolatility: 0, maxDrawdown: 0, pointCount: 0))
        }
        let weights = Dictionary(uniqueKeysWithValues: holdings.map { ($0.assetKey, valueCny($0, ctx: ctx) / total) })

        // window end = latest price date across holdings; start = end - lookback
        var latestDate = ""
        for h in holdings {
            if let last = (try? db.fetchPrices(assetKey: h.assetKey))?.last, last.date > latestDate {
                latestDate = last.date
            }
        }
        let fmt = Repository.utcDayFormatter
        let endDate = fmt.date(from: latestDate) ?? Date()
        let startDate = Calendar.current.date(byAdding: .year, value: -Int(lookbackYears.rounded()), to: endDate) ?? endDate
        let startStr = fmt.string(from: startDate)

        // normalized series per asset within the window, forward-filled on the union of dates
        var series: [String: [String: Double]] = [:]
        var dateSet = Set<String>()
        for h in holdings {
            let points = try db.fetchPrices(assetKey: h.assetKey)
            let inWindow = points.filter { $0.date >= startStr }
            guard let base = inWindow.first?.close, base > 0, inWindow.count >= 2 else { continue }
            var norm: [String: Double] = [:]
            for p in inWindow {
                norm[p.date] = p.close / base
                dateSet.insert(p.date)
            }
            series[h.assetKey] = norm
        }
        guard !dateSet.isEmpty else {
            return ([], PerformanceSummary(startDate: startStr, endDate: latestDate, totalReturn: 0,
                                           annualizedVolatility: 0, maxDrawdown: 0, pointCount: 0))
        }
        let dates = dateSet.sorted()
        var points: [PerformancePoint] = []
        var lastNorm: [String: Double] = [:]
        var runningMax = -Double.greatestFiniteMagnitude
        var maxDrawdown = 0.0
        for d in dates {
            var weighted = 0.0
            for (key, w) in weights {
                if let v = series[key]?[d] { lastNorm[key] = v }
                weighted += (lastNorm[key] ?? 1.0) * w
            }
            points.append(PerformancePoint(date: d, value: weighted))
            runningMax = max(runningMax, weighted)
            let dd = (runningMax - weighted) / runningMax
            maxDrawdown = max(maxDrawdown, dd)
        }
        guard let firstPt = points.first, let lastPt = points.last, firstPt.value > 0 else {
            return (points, PerformanceSummary(startDate: dates.first, endDate: dates.last,
                                               totalReturn: 0, annualizedVolatility: 0,
                                               maxDrawdown: maxDrawdown, pointCount: points.count))
        }
        let totalReturn = (lastPt.value / firstPt.value) - 1

        var rets: [Double] = []
        for i in 1..<points.count {
            let prev = points[i - 1].value
            if prev > 0 { rets.append(log(points[i].value / prev)) }
        }
        let annualizedVol: Double
        if rets.count > 1 {
            let mean = rets.reduce(0, +) / Double(rets.count)
            let variance = rets.reduce(0) { $0 + ($1 - mean) * ($1 - mean) } / Double(rets.count - 1)
            annualizedVol = sqrt(variance) * sqrt(252)
        } else {
            annualizedVol = 0
        }

        return (points, PerformanceSummary(startDate: firstPt.date, endDate: lastPt.date,
                                           totalReturn: totalReturn,
                                           annualizedVolatility: annualizedVol,
                                           maxDrawdown: maxDrawdown, pointCount: points.count))
    }

    // MARK: 模块2 — asset perspective

    public func fetchAssetPerspectives() throws -> [AssetPerspectiveRow] {
        let ctx = try loadContext()
        let total = ctx.totalValueCny
        let divInputs = dividendInputs()

        return ctx.holdings.compactMap { h -> AssetPerspectiveRow? in
            guard let a = ctx.byKey[h.assetKey] else { return nil }
            let value = derivedValue(h, latest: ctx.latest)
            let valueCny = value * (ctx.fx[h.currency] ?? 1.0)
            let costCny = h.costBasis * (ctx.fx[h.currency] ?? 1.0)
            let unrealizedPnl = valueCny - costCny
            let latestPrice = ctx.latest[h.assetKey]
            var latestDate: String? = nil
            if let pts = try? db.fetchPrices(assetKey: h.assetKey), let last = pts.last {
                latestDate = last.date
            }
            return AssetPerspectiveRow(
                assetKey: h.assetKey,
                name: a.name,
                assetClass: a.assetClass ?? "其他",
                market: market(a),
                pool: pool(a),
                currency: h.currency,
                value: value,
                valueCny: valueCny,
                costCny: costCny,
                unrealizedPnl: unrealizedPnl,
                returnRate: costCny > 0 ? unrealizedPnl / costCny : 0,
                quantity: h.quantity,
                costBasis: h.costBasis,
                weight: total > 0 ? valueCny / total : 0,
                latestPrice: latestPrice,
                latestDate: latestDate,
                sortOrder: a.sortOrder ?? 0,
                dividend: assetDividend(asset: a, quantity: h.quantity,
                                        currency: h.currency, fx: ctx.fx,
                                        market: market(a), inputs: divInputs))
        }.sorted {
            // 手动排序优先 (拖动后持久化); 未排序 (全部 0) 时按市值降序展示.
            if $0.sortOrder != $1.sortOrder { return $0.sortOrder < $1.sortOrder }
            return $0.valueCny > $1.valueCny
        }
    }

    // MARK: 全资产股息 / 全资产股息率

    /// 一次抓取到的股息记录 + 元数据 (避免逐个资产查库).
    private struct DividendInputs {
        let recordsByAsset: [String: [DividendRecord]]
        let metas: [String: DividendFetchMeta]
        let currentYear: Int
    }

    private func dividendInputs() -> DividendInputs {
        let records = (try? db.fetchDividends()) ?? []
        let metas = (try? db.fetchDividendFetchMetas()) ?? [:]
        return DividendInputs(
            recordsByAsset: Dictionary(grouping: records, by: { $0.assetKey }),
            metas: metas,
            currentYear: Calendar.current.component(.year, from: Date()))
    }

    /// 单个资产的股息派生 (口径见决策 Q5/Q14/Q18/Q21/Q28/Q35).
    ///
    /// - 前年股息 = 前年除息的每股股息合计 × 份额 (原币, 税前)
    /// - 去年股息 = 去年除息的每股股息合计 × 份额 (原币, 税前)
    /// - 今年预计股息(原币, 税前) = max(0, 2×去年 − 前年) × 份额 (负值截断)
    /// - 今年预计股息(折人民币, 税后) = 上式 × 汇率 × (1 − 税率)
    /// - 今年已发生股息(折人民币, 税后) = 本自然年已除息合计 × 份额 × 汇率 × (1 − 税率)
    ///
    /// 状态非 `ok` (数据源无覆盖 / 抓取失败 / 从未抓取) 时, 四个金额字段全为 nil
    /// (详情显示「—」)。注意: 新上市标的只要**任一抓取窗口**有数据就算 `ok` ——
    /// 例如某只当年新上市的标的会命中当年窗口, 因此显示 ¥0.00 而不是「—」
    /// (它在 2024/2025 确实没有派息, 数值上与 NULL 态一样按 0 计入)。
    private func assetDividend(asset: Asset, quantity: Double, currency: String,
                               fx: [String: Double], market: AssetMarket?,
                               inputs: DividendInputs) -> AssetDividend {
        let meta = inputs.metas[asset.key]
        let status = meta?.status ?? .unavailable
        let ok = (status == .ok)
        let records = ok ? (inputs.recordsByAsset[asset.key] ?? []) : []
        let yearPrevPrev = inputs.currentYear - 2
        let yearPrev = inputs.currentYear - 1
        let yearCurrent = inputs.currentYear

        func perShare(_ year: Int) -> Double {
            records.filter { $0.year == year }.reduce(0) { $0 + $1.amount }
        }
        let psPrevPrev = perShare(yearPrevPrev)
        let psPrev = perShare(yearPrev)
        let estPerShare = max(0, 2 * psPrev - psPrevPrev)

        // 税率与池归属共用同一个解析结果 (Q31): market 来自 AssetMarket，币种回退只在解析不出市场时发生。
        let taxRate = DividendTax.rate(market: market?.rawValue, currency: currency)
        let fxRate = fx[currency]
        var fxMissing = false
        var estimatedCny: Double?
        var actualCny: Double?
        if ok {
            if let fxRate, fxRate > 0 {
                let net = 1 - (taxRate ?? 0)
                estimatedCny = estPerShare * quantity * fxRate * net
                actualCny = perShare(yearCurrent) * quantity * fxRate * net
            } else {
                fxMissing = true
            }
        }

        return AssetDividend(
            status: status,
            source: meta?.source,
            fetchedAt: meta?.fetchedAt,
            yearPrevPrev: yearPrevPrev, yearPrev: yearPrev, yearCurrent: yearCurrent,
            perSharePrevPrev: psPrevPrev, perSharePrev: psPrev,
            prevPrevTotal: ok ? psPrevPrev * quantity : nil,
            prevTotal: ok ? psPrev * quantity : nil,
            estimatedTotal: ok ? estPerShare * quantity : nil,
            estimatedTotalCny: estimatedCny,
            actualYtdCny: actualCny,
            taxRate: taxRate, fxMissing: fxMissing,
            quantity: quantity, currency: currency)
    }

    /// 全资产股息 = Σ 各资产今年预计股息 (税后, 折人民币); NULL 态与缺汇率按 0 计入.
    /// 覆盖率只统计非 NULL 态 (决策 Q12), 因为 NULL 计 0 会系统性低估该指标。
    public func fetchDividendSummary() throws -> DividendSummary {
        let ctx = try loadContext()
        let inputs = dividendInputs()
        var total = 0.0
        var covered = 0
        var latest: String?
        for h in ctx.holdings {
            guard let a = ctx.byKey[h.assetKey] else { continue }
            let d = assetDividend(asset: a, quantity: h.quantity,
                                  currency: h.currency, fx: ctx.fx,
                                  market: market(a), inputs: inputs)
            if d.status == .ok { covered += 1 }
            total += d.estimatedTotalCny ?? 0
            if let f = d.fetchedAt, f > (latest ?? "") { latest = f }
        }
        return DividendSummary(totalNetCny: total, coveredCount: covered,
                               assetCount: ctx.holdings.count, fetchedAt: latest)
    }

    // MARK: 能力4 — 财务分析 (个人资产/收益结构)

    public func fetchIncomeSummaries() throws -> [IncomeSummary] {
        try db.fetchIncomeSummaries()
    }

    public func fetchQuarterlyReports() throws -> [QuarterlyReport] {
        try db.fetchQuarterlyReports()
    }

    public func fetchFinancialAnalysis() throws -> FinancialAnalysis {
        let ctx = try loadContext()
        let originalPrincipal = ctx.holdings.reduce(0.0) { $0 + $1.costBasis * (ctx.fx[$1.currency] ?? 1.0) }
        let marketValue = ctx.totalValueCny
        let periods = try db.fetchIncomeSummaries()
        let totalDividends = periods.reduce(0.0) { $0 + $1.dividends }
        let totalRealized = periods.reduce(0.0) { $0 + $1.realizedPnl }
        let realizedPnl = totalDividends + totalRealized          // 实盈实亏
        let principal = originalPrincipal + realizedPnl           // 本金 = 原始本金 + 实盈实亏
        let unrealized = marketValue - principal                  // 未实现资本利得 = 市值 - 本金
        let totalIncome = realizedPnl + unrealized                // 合计收益 = 市值 - 原始本金
        // 收益率 = 实盈实亏 / 原始本金 (已实现收益率, 与资产结构卡片对应)
        let returnRate = originalPrincipal > 0 ? realizedPnl / originalPrincipal : 0
        return FinancialAnalysis(
            originalPrincipal: originalPrincipal,
            realizedPnl: realizedPnl,
            principal: principal,
            marketValue: marketValue,
            unrealizedPnl: unrealized,
            returnRate: returnRate,
            totalDividends: totalDividends,
            totalRealizedPnl: totalRealized,
            totalIncome: totalIncome,
            totalReturnRate: originalPrincipal > 0 ? totalIncome / originalPrincipal : 0,
            periods: periods)
    }
}
