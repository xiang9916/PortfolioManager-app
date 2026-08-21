import Foundation

public struct Quote: Codable, Hashable {
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
}

extension DataSource {
    /// Default quote = last point of history.
    public func fetchQuote(symbol: String) async throws -> Quote {
        let hist = try await fetchHistory(symbol: symbol)
        guard let last = hist.last else { throw DataSourceError.empty(symbol) }
        return Quote(symbol: symbol, price: last.close, currency: last.currency,
                     date: last.date, source: name)
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
