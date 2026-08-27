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
/// 境内/境外/跨池 按每个标的自身的 pool 字段统计 (与资产透视中填写的一致),
/// 不再按资产大类分组取整组归属.
public struct AllocationSnapshot: Codable, Hashable {
    public let asOfDate: String?
    public let totalValue: Double
    public let domesticValue: Double
    public let overseasValue: Double
    public let crossValue: Double
    public let slices: [AllocationSlice]

    public init(asOfDate: String?, totalValue: Double, domesticValue: Double,
                overseasValue: Double, crossValue: Double = 0, slices: [AllocationSlice]) {
        self.asOfDate = asOfDate
        self.totalValue = totalValue
        self.domesticValue = domesticValue
        self.overseasValue = overseasValue
        self.crossValue = crossValue
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
    public let pool: Pool
    /// Holding currency (the currency `costBasis` is entered in; the derived value shares it).
    public let currency: String
    /// 市值(标的币种) = 份额 × 最后价 (派生, 不手填).
    public let value: Double
    /// 市值折人民币 (value × FX rate) — 权重计算基准.
    public let valueCny: Double
    /// 成本/本金折人民币 (costBasis × FX rate).
    public let costCny: Double
    /// 浮盈浮亏 = 市值 - 本金 (人民币).
    public let unrealizedPnl: Double
    /// 收益率 = 浮盈浮亏 / 本金 (本金为 0 时记 0).
    public let returnRate: Double
    public let quantity: Double
    public let costBasis: Double
    public let weight: Double
    public let latestPrice: Double?
    public let latestDate: String?
    /// 手动排序序号 (资产透视拖动排序).
    public let sortOrder: Double
    public var id: String { assetKey }
}

/// 能力4 财务分析: 个人资产/收益结构底稿 (组合级).
public struct FinancialAnalysis: Codable, Hashable {
    // 资产结构 (恒等式: 市值 = 原始本金 + 实盈实亏 + 浮盈浮亏, 其中 本金 = 原始本金 + 实盈实亏)
    public let originalPrincipal: Double // 原始本金 = Σ 成本×汇率
    public let realizedPnl: Double       // 实盈实亏 = 累计股息分红 + 累计交易损益
    public let principal: Double         // 本金 = 原始本金 + 实盈实亏
    public let marketValue: Double       // 市值 = Σ 市值折¥
    public let unrealizedPnl: Double     // 浮盈浮亏 = 市值 - 本金
    public let returnRate: Double        // 收益率 = 浮盈浮亏 / 本金
    // 收益结构 (从 income_periods 累计)
    public let totalDividends: Double   // 累计股息分红
    public let totalRealizedPnl: Double // 累计交易损益
    public let totalIncome: Double      // 合计收益 = 实盈实亏 + 浮盈浮亏 = 市值 - 原始本金
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

    // MARK: 模块1 — asset allocation

    public func fetchAllocation() throws -> AllocationSnapshot {
        let ctx = try loadContext()
        let total = ctx.totalValueCny
        var groups: [String: (value: Double, pool: Pool)] = [:]
        // 池统计按「每个标的自身的 pool」累计 — 修复: 之前按大类分组且组池被同组
        // 最后一个标的覆盖, 导致同大类中境内标的 (如 021550) 被计入境外池.
        var domestic = 0.0
        var overseas = 0.0
        var cross = 0.0
        for h in ctx.holdings {
            let a = ctx.byKey[h.assetKey]
            let cls = a?.assetClass ?? "其他"
            let pool = a?.pool ?? .overseas
            let v = valueCny(h, ctx: ctx)
            if let cur = groups[cls] {
                groups[cls] = (cur.value + v, cur.pool)
            } else {
                groups[cls] = (v, pool)
            }
            switch pool {
            case .domestic: domestic += v
            case .overseas: overseas += v
            case .cross: cross += v
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
            crossValue: cross,
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
                pool: a.pool,
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
                sortOrder: a.sortOrder ?? 0)
        }.sorted {
            // 手动排序优先 (拖动后持久化); 未排序 (全部 0) 时按市值降序展示.
            if $0.sortOrder != $1.sortOrder { return $0.sortOrder < $1.sortOrder }
            return $0.valueCny > $1.valueCny
        }
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
        let unrealized = marketValue - principal                  // 浮盈浮亏 = 市值 - 本金
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
