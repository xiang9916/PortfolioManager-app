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
public struct AllocationSnapshot: Codable, Hashable {
    public let asOfDate: String?
    public let totalValue: Double
    public let domesticValue: Double
    public let overseasValue: Double
    public let slices: [AllocationSlice]
}

/// One point of the reconstructed portfolio NAV (weighted index).
public struct PerformancePoint: Codable, Hashable, Identifiable {
    public let date: String
    public let value: Double
    public var id: String { date }
}

/// Summary statistics for historical performance (能力1 历史财务表现).
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
    /// Holding currency (the currency `value` / `costBasis` are entered in).
    public let currency: String
    /// Market value in the holding currency (e.g. USD).
    public let value: Double
    /// Market value converted to CNY (value × FX rate) — basis for weight.
    public let valueCny: Double
    public let quantity: Double
    public let costBasis: Double
    public let weight: Double
    public let latestPrice: Double?
    public let latestDate: String?
    public var id: String { assetKey }
}

/// High-level query layer: composes raw Database rows into UI-ready structures.
/// All methods are synchronous; call from a single (main-actor) context.
public final class Repository {
    public let db: Database

    public init(db: Database) {
        self.db = db
    }

    // MARK: FX — 能力2 权重统一成人民币

    /// Load currency→CNY rates; CNY is always 1.0.
    private func fxRates() -> [String: Double] {
        var map = Dictionary(uniqueKeysWithValues: (try? db.fetchFxRates())?.map { ($0.currency, $0.rateToCny) } ?? [])
        map["CNY"] = 1.0
        return map
    }

    /// A holding's value converted to CNY (missing rate falls back to 1.0).
    private func cny(_ h: Holding, fx: [String: Double]) -> Double {
        h.value * (fx[h.currency] ?? 1.0)
    }

    // MARK: 模块1 — asset allocation

    public func fetchAllocation() throws -> AllocationSnapshot {
        let assets = try db.fetchAssets()
        let byKey = Dictionary(uniqueKeysWithValues: assets.map { ($0.key, $0) })
        let holdings = try db.fetchHoldings()
        let fx = fxRates()

        let total = holdings.reduce(0.0) { $0 + cny($1, fx: fx) }
        var groups: [String: (value: Double, pool: Pool)] = [:]
        for h in holdings {
            let cls = byKey[h.assetKey]?.assetClass ?? "其他"
            let pool = byKey[h.assetKey]?.pool ?? .overseas
            let cur = groups[cls] ?? (0, pool)
            groups[cls] = (cur.value + cny(h, fx: fx), pool)
        }
        let slices = groups.map { (cls, v) in
            AllocationSlice(assetClass: cls, value: v.value,
                            weight: total > 0 ? v.value / total : 0, pool: v.pool)
        }.sorted { $0.value > $1.value }

        let latest = try db.fetchSnapshots().last
        let domestic = slices.filter { $0.pool == .domestic }.reduce(0.0) { $0 + $1.value }
        let overseas = total - domestic

        return AllocationSnapshot(
            asOfDate: latest?.date ?? holdings.first?.asOfDate,
            totalValue: total,
            domesticValue: latest?.domesticValue ?? domestic,
            overseasValue: latest?.overseasValue ?? overseas,
            slices: slices)
    }

    // MARK: 能力1 — historical performance (weighted NAV reconstruction)

    /// Reconstructs a trailing weighted-NAV series from current holdings + price history.
    /// Each asset is rebased to 1.0 at its first observation inside a trailing window
    /// (default 3 years) and weighted by its current CNY value. Cross-currency is ignored
    /// (FX is not embedded), so the series reflects relative weighted performance.
    public func fetchPerformance(lookbackYears: Double = 3.0) throws -> (points: [PerformancePoint], summary: PerformanceSummary) {
        let holdings = try db.fetchHoldings()
        let fx = fxRates()
        let total = holdings.reduce(0.0) { $0 + cny($1, fx: fx) }
        guard total > 0, !holdings.isEmpty else {
            return ([], PerformanceSummary(startDate: nil, endDate: nil, totalReturn: 0,
                                           annualizedVolatility: 0, maxDrawdown: 0, pointCount: 0))
        }
        let weights = Dictionary(uniqueKeysWithValues: holdings.map { ($0.assetKey, cny($0, fx: fx) / total) })

        // window end = latest price date across holdings; start = end - lookback
        var latestDate = ""
        for h in holdings {
            if let last = (try? db.fetchPrices(assetKey: h.assetKey))?.last, last.date > latestDate {
                latestDate = last.date
            }
        }
        let fmt = DateFormatter()
        fmt.dateFormat = "yyyy-MM-dd"
        fmt.timeZone = TimeZone(identifier: "UTC")
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
        let assets = try db.fetchAssets()
        let byKey = Dictionary(uniqueKeysWithValues: assets.map { ($0.key, $0) })
        let holdings = try db.fetchHoldings()
        let fx = fxRates()
        let total = holdings.reduce(0.0) { $0 + cny($1, fx: fx) }

        return holdings.compactMap { h -> AssetPerspectiveRow? in
            guard let a = byKey[h.assetKey] else { return nil }
            let valueCny = cny(h, fx: fx)
            var latestPrice: Double? = nil
            var latestDate: String? = nil
            if let pts = try? db.fetchPrices(assetKey: h.assetKey), let last = pts.last {
                latestPrice = last.close
                latestDate = last.date
            }
            return AssetPerspectiveRow(
                assetKey: h.assetKey,
                name: a.name,
                assetClass: a.assetClass ?? "其他",
                pool: a.pool,
                currency: h.currency,
                value: h.value,
                valueCny: valueCny,
                quantity: h.quantity,
                costBasis: h.costBasis,
                weight: total > 0 ? valueCny / total : 0,
                latestPrice: latestPrice,
                latestDate: latestDate)
        }.sorted { $0.valueCny > $1.valueCny }
    }

    // MARK: 能力4 — financial statements

    public func fetchFinancialComparison() throws -> [Financial] {
        try db.fetchFinancials()
    }
}
