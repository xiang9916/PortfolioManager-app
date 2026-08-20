import Foundation

public struct Quote: Codable, Hashable {
    public let symbol: String
    public let price: Double
    public let currency: String?
    public let date: String
    public let source: String
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
public protocol DataSource {
    var name: String { get }
    func fetchHistory(symbol: String) async throws -> [PricePoint]
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
    public static func dateString(fromTimestampSeconds ts: Int, timeZone: String = "UTC") -> String {
        let d = Date(timeIntervalSince1970: TimeInterval(ts))
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.timeZone = TimeZone(identifier: timeZone)
        return f.string(from: d)
    }
    public static func dateString(fromTimestampMillis ts: Int64, timeZone: String = "UTC") -> String {
        let d = Date(timeIntervalSince1970: TimeInterval(ts) / 1000.0)
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.timeZone = TimeZone(identifier: timeZone)
        return f.string(from: d)
    }
}
