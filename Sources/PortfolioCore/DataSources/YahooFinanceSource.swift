import Foundation

public final class YahooFinanceSource: DataSource {
    public let name = "yahoo"
    private let session: URLSession

    public init() {
        let config = URLSessionConfiguration.default
        config.httpAdditionalHeaders = ["User-Agent": "Mozilla/5.0"]
        self.session = URLSession(configuration: config)
    }

    public func fetchHistory(symbol: String) async throws -> [PricePoint] {
        let end = Int(Date().timeIntervalSince1970)
        let start = 0  // earliest available daily history
        let urlStr = "https://query1.finance.yahoo.com/v8/finance/chart/\(symbol)?period1=\(start)&period2=\(end)&interval=1d"
        guard let url = URL(string: urlStr) else { throw DataSourceError.empty(symbol) }
        let (data, response) = try await session.data(from: url)
        if let http = response as? HTTPURLResponse, http.statusCode != 200 {
            throw DataSourceError.http(http.statusCode, urlStr)
        }
        let decoded = try JSONDecoder().decode(YahooChartResponse.self, from: data)
        guard let r = decoded.chart.result.first else { return [] }
        let closes = r.indicators.quote.first?.close ?? []
        var out: [PricePoint] = []
        for (i, ts) in r.timestamp.enumerated() {
            guard i < closes.count, let c = closes[i] else { continue }
            out.append(PricePoint(assetKey: symbol,
                                  date: DateUtil.dateString(fromTimestampSeconds: ts),
                                  close: c, currency: "USD"))
        }
        return out
    }
}

struct YahooChartResponse: Decodable {
    struct Chart: Decodable {
        struct Result: Decodable {
            let timestamp: [Int]
            let indicators: Indicators
        }
        struct Indicators: Decodable {
            let quote: [Quote]
        }
        struct Quote: Decodable {
            let close: [Double?]
        }
        let result: [Result]
    }
    let chart: Chart
}
