import Foundation

// MARK: - UI view models (shared across SwiftUI views)
//
// AllocationSlice / AllocationSnapshot 已迁到 Query/PortfolioValuation.swift：
// 它们现在是「组合估值」的投影，不再是 Repository 自己算的一份。

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

/// High-level query layer: composes raw Database rows into UI-ready structures.
/// 估值口径集中在 `PortfolioValuation`；本类只负责读库与投影。
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

    // MARK: FX

    /// Load currency→CNY rates; CNY is always 1.0.
    private func fxRates() -> [String: Double] {
        var map = Dictionary(uniqueKeysWithValues: (try? db.fetchFxRates())?.map { ($0.currency, $0.rateToCny) } ?? [])
        map["CNY"] = 1.0
        return map
    }

    // MARK: - 组合估值（唯一入口）

    /// 读一次库，产出组合估值。所有读模型都从这里派生。
    ///
    /// 最新价：`quotes` 优先；缺有效 quote 的持仓才用 `fetchLastPrice` 取最后一根 K 线
    /// （有 quote 时价格与日期都取 quote，不再读历史）。
    public func loadValuation() throws -> PortfolioValuation {
        let assets = try db.fetchAssets()
        let byKey = Dictionary(uniqueKeysWithValues: assets.map { ($0.key, $0) })
        let holdings = try db.fetchHoldings()
        let quotes = try db.fetchLatestQuotes()
        let fx = fxRates()

        var lastPrices: [String: PricePoint] = [:]
        for h in holdings {
            let quote = quotes[h.assetKey]
            let quoteValid = quote.map { $0.price.isFinite && $0.price > 0 } ?? false
            if quoteValid { continue }
            if let bar = try db.fetchLastPrice(assetKey: h.assetKey) {
                lastPrices[h.assetKey] = bar
            }
        }

        return PortfolioValuation.make(holdings: holdings, assetsByKey: byKey,
                                       quotes: quotes, lastPrices: lastPrices,
                                       fxRates: fx)
    }

    /// 已加载的估值优先；没有就现读一次。
    private func resolve(_ valuation: PortfolioValuation?) throws -> PortfolioValuation {
        if let valuation { return valuation }
        return try loadValuation()
    }

    // MARK: 模块1 — asset allocation

    public func fetchAllocation() throws -> AllocationSnapshot {
        try loadValuation().allocationSnapshot
    }

    // MARK: 能力1 — historical performance (weighted NAV reconstruction)

    /// Reconstructs a trailing weighted-NAV series from current holdings + price history.
    /// Each asset is rebased to 1.0 at its first observation inside a trailing window
    /// (default 3 years) and weighted by its current derived CNY value. Cross-currency is ignored
    /// (FX is not embedded), so the series reflects relative weighted performance.
    public func fetchPerformance(lookbackYears: Double = 3.0,
                                 valuation: PortfolioValuation? = nil) throws
        -> (points: [PerformancePoint], summary: PerformanceSummary) {
        let v = try resolve(valuation)
        let positions = v.positions
        guard v.totalValueCny > 0, !positions.isEmpty else {
            return ([], PerformanceSummary(startDate: nil, endDate: nil, totalReturn: 0,
                                           annualizedVolatility: 0, maxDrawdown: 0, pointCount: 0))
        }
        let weights = Dictionary(positions.map { ($0.assetKey, $0.weight) },
                                 uniquingKeysWith: { first, _ in first })

        // window end = latest price date across holdings; start = end - lookback
        var latestDate = ""
        for p in positions {
            if let last = (try? db.fetchPrices(assetKey: p.assetKey))?.last, last.date > latestDate {
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
        for p in positions {
            let points = try db.fetchPrices(assetKey: p.assetKey)
            let inWindow = points.filter { $0.date >= startStr }
            guard let base = inWindow.first?.close, base > 0, inWindow.count >= 2 else { continue }
            var norm: [String: Double] = [:]
            for pt in inWindow {
                norm[pt.date] = pt.close / base
                dateSet.insert(pt.date)
            }
            series[p.assetKey] = norm
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
                if let nv = series[key]?[d] { lastNorm[key] = nv }
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

    // MARK: 特别关注 — 合成日 K（第 4 个模块）

    /// 组装「特别关注」页所需的数据：组合合成日 K（20 根）+ 当日涨幅/跌幅最高两只。
    ///
    /// 只做取数与装配；口径（归一化、逐项加权、无日内价的降级）全部在 FocusComposer 里，
    /// 与视图层无关、可脱离数据库单测。没有历史价格时返回 .empty，由视图给出引导文案。
    public func fetchFocusBoard(candleCount: Int = FocusComposer.defaultCandleCount,
                                valuation: PortfolioValuation? = nil) throws -> FocusBoard {
        let v = try resolve(valuation)
        var series: [FocusSeries] = []
        for p in v.positions {
            guard p.valueCny > 0 else { continue }
            let bars = (try? db.fetchPrices(assetKey: p.assetKey)) ?? []
            guard !bars.isEmpty else { continue }
            series.append(FocusSeries(assetKey: p.assetKey, name: p.name,
                                      weight: p.valueCny, bars: bars))
        }
        return FocusComposer.compose(series: series, candleCount: candleCount)
    }

    // MARK: 模块2 — asset perspective

    public func fetchAssetPerspectives(valuation: PortfolioValuation? = nil) throws -> [AssetPerspectiveRow] {
        let v = try resolve(valuation)
        let divInputs = dividendInputs()

        return v.positions.compactMap { p -> AssetPerspectiveRow? in
            guard p.hasAsset else { return nil }
            return AssetPerspectiveRow(
                assetKey: p.assetKey,
                name: p.name,
                assetClass: p.assetClass,
                market: p.market,
                pool: p.pool,
                currency: p.currency,
                value: p.valueLocal,
                valueCny: p.valueCny,
                costCny: p.costCny,
                unrealizedPnl: p.unrealizedPnlCny,
                returnRate: p.returnRate,
                quantity: p.quantity,
                costBasis: p.costBasis,
                weight: p.weight,
                latestPrice: p.priceLocal,
                latestDate: p.priceDate,
                sortOrder: p.sortOrder,
                dividend: assetDividend(assetKey: p.assetKey, quantity: p.quantity,
                                        currency: p.currency, fxRate: p.fxRate,
                                        market: p.market, inputs: divInputs))
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
    private func assetDividend(assetKey: String, quantity: Double, currency: String,
                               fxRate: Double?, market: AssetMarket?,
                               inputs: DividendInputs) -> AssetDividend {
        let meta = inputs.metas[assetKey]
        let status = meta?.status ?? .unavailable
        let ok = (status == .ok)
        let records = ok ? (inputs.recordsByAsset[assetKey] ?? []) : []
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
        var fxMissing = false
        var estimatedCny: Double?
        var actualCny: Double?
        if ok {
            if let fxRate, fxRate.isFinite, fxRate > 0 {
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
    public func fetchDividendSummary(valuation: PortfolioValuation? = nil) throws -> DividendSummary {
        let v = try resolve(valuation)
        let inputs = dividendInputs()
        var total = 0.0
        var covered = 0
        var latest: String?
        for p in v.positions {
            guard p.hasAsset else { continue }
            let d = assetDividend(assetKey: p.assetKey, quantity: p.quantity,
                                  currency: p.currency, fxRate: p.fxRate,
                                  market: p.market, inputs: inputs)
            if d.status == .ok { covered += 1 }
            total += d.estimatedTotalCny ?? 0
            if let f = d.fetchedAt, f > (latest ?? "") { latest = f }
        }
        return DividendSummary(totalNetCny: total, coveredCount: covered,
                               assetCount: v.positions.count, fetchedAt: latest)
    }

    // MARK: 能力4 — 财务分析 (个人资产/收益结构)

    public func fetchIncomeSummaries() throws -> [IncomeSummary] {
        try db.fetchIncomeSummaries()
    }

    public func fetchQuarterlyReports() throws -> [QuarterlyReport] {
        try db.fetchQuarterlyReports()
    }

    public func fetchFinancialAnalysis(valuation: PortfolioValuation? = nil) throws -> FinancialAnalysis {
        let v = try resolve(valuation)
        let originalPrincipal = v.positions.reduce(0.0) { $0 + $1.costCny }
        let marketValue = v.totalValueCny
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
