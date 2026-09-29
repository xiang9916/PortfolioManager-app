import Foundation

public struct Quote: Codable, Hashable, Sendable {
    public let symbol: String
    public let price: Double
    public let currency: String?
    public let date: String
    public let source: String

    public init(symbol: String, price: Double, currency: String?,
                date: String, source: String) {
        self.symbol = symbol
        self.price = price
        self.currency = currency
        self.date = date
        self.source = source
    }
}

public enum DataSourceError: Error, CustomStringConvertible {
    case empty(String)
    case http(Int, String)

    /// `empty` = 数据源对该标的无数据 (股息里 = 无历史覆盖, NULL 态)。
    public var isUnavailable: Bool {
        if case .empty = self { return true }
        return false
    }

    public var description: String {
        switch self {
        case .empty(let s): return "no data for \(s)"
        case .http(let c, let u): return "HTTP \(c) \(u)"
        }
    }
}

/// A single market-data source (能力1). Each source knows how to fetch history + quote.
public protocol DataSource: Sendable {
    var name: String { get }
    func fetchHistory(symbol: String) async throws -> [PricePoint]
    /// Latest quote (unit price for market-value calculation).
    /// Funds return unit NAV (单位净值); stocks/ETFs return latest close.
    func fetchQuote(symbol: String) async throws -> Quote
    /// 逐笔每股/每份现金股息 (税前, 标的报价币种), 用于「全资产股息」.
    ///
    /// - Parameter years: 需要覆盖的自然年 (含当年). 实现方必须**按自然年边界逐年请求**
    ///   再合并, 不要用 `range=max`: 实测 Yahoo 的 `range=max` 用的是复权口径,
    ///   与逐年窗口求和的结果不一致 (AAPL 2024: 1.00 vs 0.99)。
    ///
    /// 返回空数组 = 抓取成功但该标的确实无分红 (0 态)。
    /// 抛 `DataSourceError.empty` = 数据源对该标的无历史覆盖 (NULL 态)。
    /// 抛其它错误 = 抓取失败 (NULL 态, 已抓到的历史记录保留)。
    func fetchDividends(symbol: String, years: [Int]) async throws -> [DividendEvent]
}

extension DataSource {
    /// Default quote = last point of history.
    public func fetchQuote(symbol: String) async throws -> Quote {
        let hist = try await fetchHistory(symbol: symbol)
        guard let last = hist.last else { throw DataSourceError.empty(symbol) }
        return Quote(symbol: symbol, price: last.close, currency: last.currency,
                     date: last.date, source: name)
    }

    /// Default: 数据源不提供股息 (按 0 态处理, 不误报为数据源无覆盖).
    public func fetchDividends(symbol: String, years: [Int]) async throws -> [DividendEvent] {
        []
    }
}

/// Shared date helpers.
public enum DateUtil {
    /// UTC formatter (the common case — avoids creating a new DateFormatter per call).
    private static let utcFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.timeZone = TimeZone(identifier: "UTC")
        return f
    }()

    private static func formatter(for tz: String) -> DateFormatter {
        if tz == "UTC" { return utcFormatter }
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.timeZone = TimeZone(identifier: tz)
        return f
    }

    public static func dateString(fromTimestampSeconds ts: Int, timeZone: String = "UTC") -> String {
        let d = Date(timeIntervalSince1970: TimeInterval(ts))
        return formatter(for: timeZone).string(from: d)
    }
    public static func dateString(fromTimestampMillis ts: Int64, timeZone: String = "UTC") -> String {
        let d = Date(timeIntervalSince1970: TimeInterval(ts) / 1000.0)
        return formatter(for: timeZone).string(from: d)
    }
}
