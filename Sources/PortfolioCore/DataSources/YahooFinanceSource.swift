import Foundation

public final class YahooFinanceSource: DataSource {
    public let name = "yahoo"
    private let session: URLSession

    public init() {
        let config = URLSessionConfiguration.default
        config.httpAdditionalHeaders = ["User-Agent": "Mozilla/5.0"]
        self.session = URLSession(configuration: config)
    }

    /// Result of validating / resolving a ticker symbol (能力2 联网校验).
    public struct SymbolInfo: Hashable {
        public let symbol: String
        public let currency: String
        public let name: String?
        public let price: Double?
    }

    private func chartURL(symbol: String, start: Int, end: Int) -> URL? {
        URL(string: "https://query1.finance.yahoo.com/v8/finance/chart/" + symbol + "?period1=" + String(start) + "&period2=" + String(end) + "&interval=1d")
    }

    private func fetchChart(symbol: String, start: Int, end: Int) async throws -> YahooChartResponse {
        guard let url = chartURL(symbol: symbol, start: start, end: end) else {
            throw DataSourceError.empty(symbol)
        }
        let (data, response) = try await session.data(from: url)
        if let http = response as? HTTPURLResponse, http.statusCode != 200 {
            throw DataSourceError.http(http.statusCode, url.absoluteString)
        }
        return try JSONDecoder().decode(YahooChartResponse.self, from: data)
    }

    /// Validate a ticker and resolve its currency / name / last price.
    /// Throws DataSourceError.empty when Yahoo has no such symbol (联网校验失败).
    public func lookup(symbol: String) async throws -> SymbolInfo {
        let end = Int(Date().timeIntervalSince1970)
        let start = end - 10 * 86400  // last 10 days
        let decoded = try await fetchChart(symbol: symbol, start: start, end: end)
        guard let r = decoded.chart.result?.first, let meta = r.meta else {
            throw DataSourceError.empty(symbol)
        }
        return SymbolInfo(
            symbol: meta.symbol ?? symbol,
            currency: meta.currency ?? "USD",
            name: meta.longName ?? meta.shortName,
            price: meta.regularMarketPrice)
    }

    public func fetchHistory(symbol: String) async throws -> [PricePoint] {
        let end = Int(Date().timeIntervalSince1970)
        let start = 0  // earliest available daily history
        let decoded = try await fetchChart(symbol: symbol, start: start, end: end)
        guard let r = decoded.chart.result?.first else { return [] }
        let currency = r.meta?.currency ?? "USD"
        let closes = r.indicators.quote.first?.close ?? []
        var out: [PricePoint] = []
        for (i, ts) in r.timestamp.enumerated() {
            guard i < closes.count, let c = closes[i] else { continue }
            out.append(PricePoint(assetKey: symbol,
                                  date: DateUtil.dateString(fromTimestampSeconds: ts),
                                  close: c, currency: currency))
        }
        return out
    }
}

struct YahooChartResponse: Decodable {
    struct Chart: Decodable {
        struct Result: Decodable {
            struct Meta: Decodable {
                let currency: String?
                let symbol: String?
                let regularMarketPrice: Double?
                let longName: String?
                let shortName: String?
            }
            let meta: Meta?
            let timestamp: [Int]
            let indicators: Indicators
        }
        struct Indicators: Decodable {
            let quote: [Quote]
        }
        struct Quote: Decodable {
            let close: [Double?]
        }
        let result: [Result]?
    }
    let chart: Chart
}
