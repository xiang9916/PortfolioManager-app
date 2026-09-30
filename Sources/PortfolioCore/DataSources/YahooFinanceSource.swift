import Foundation

public final class YahooFinanceSource: DataSource, Sendable {
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

    // MARK: - 股息 (events=div)

    /// 逐年拉取每股现金股息 (除息日 + 税前金额, 报价币种).
    ///
    /// 实测 (2026-09): AAPL 2024=0.99/2025=1.03, 600519.SS 54.758/51.630,
    /// 0700.HK 3.4/4.5, BTC-USD `events: []` (本无分红), 当年新上市的标的
    /// `result: null` (无历史覆盖)。
    public func fetchDividends(symbol: String, years: [Int]) async throws -> [DividendEvent] {
        var out: [DividendEvent] = []
        var okWindows = 0
        var lastError: Error?
        let now = Int(Date().timeIntervalSince1970)
        for year in years {
            let start = Self.epochJan1(year)
            let end = min(Self.epochJan1(year + 1), now + 86_400)
            guard end > start else { continue }
            do {
                let decoded = try await fetchDividendChart(symbol: symbol, start: start, end: end)
                // result == nil ⇒ 该窗口没有历史 (新上市/退市); 不算“抓取成功”.
                guard let r = decoded.chart.result?.first else { continue }
                okWindows += 1
                let currency = r.meta?.currency ?? "USD"
                for d in (r.events?.dividends ?? [:]).values {
                    guard let amount = d.amount, amount != 0, let ts = d.date else { continue }
                    out.append(DividendEvent(
                        exDate: DateUtil.dateString(fromTimestampSeconds: ts),
                        amount: amount, currency: currency))
                }
            } catch {
                lastError = error
            }
        }
        if okWindows == 0 {
            // 所有窗口都没有数据 → 数据源对该标的无覆盖 (NULL 态).
            throw lastError ?? DataSourceError.empty(symbol)
        }
        // 同一天可能被多个窗口覆盖 (跨年除息), 按除息日去重求和.
        var byDate: [String: DividendEvent] = [:]
        for e in out {
            if let cur = byDate[e.exDate] {
                byDate[e.exDate] = DividendEvent(exDate: e.exDate,
                                                 amount: cur.amount + e.amount,
                                                 currency: e.currency)
            } else {
                byDate[e.exDate] = e
            }
        }
        return byDate.values.sorted { $0.exDate < $1.exDate }
    }

    private func dividendChartURL(symbol: String, start: Int, end: Int) -> URL? {
        URL(string: "https://query1.finance.yahoo.com/v8/finance/chart/" + symbol
            + "?period1=" + String(start) + "&period2=" + String(end)
            + "&interval=1d&events=div")
    }

    private func fetchDividendChart(symbol: String, start: Int, end: Int) async throws -> YahooDividendResponse {
        guard let url = dividendChartURL(symbol: symbol, start: start, end: end) else {
            throw DataSourceError.empty(symbol)
        }
        let (data, response) = try await session.data(from: url)
        if let http = response as? HTTPURLResponse, http.statusCode != 200 {
            throw DataSourceError.http(http.statusCode, url.absoluteString)
        }
        return try JSONDecoder().decode(YahooDividendResponse.self, from: data)
    }

    /// UTC epoch seconds for Jan 1 of `year` (自然年边界, 决策 Q29).
    static func epochJan1(_ year: Int) -> Int {
        var comps = DateComponents()
        comps.year = year; comps.month = 1; comps.day = 1
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "UTC") ?? .gmt
        let d = cal.date(from: comps) ?? Date(timeIntervalSince1970: 0)
        return Int(d.timeIntervalSince1970)
    }
}

/// 只解 `events.dividends` 的响应体 —— 与行情用的 `YahooChartResponse` 分开,
/// 避免给既有解析路径引入可选字段风险。
struct YahooDividendResponse: Decodable {
    struct Chart: Decodable {
        struct Result: Decodable {
            struct Meta: Decodable {
                let currency: String?
            }
            struct Events: Decodable {
                struct Dividend: Decodable {
                    let amount: Double?
                    let date: Int?
                }
                let dividends: [String: Dividend]?
            }
            let meta: Meta?
            let events: Events?
        }
        let result: [Result]?
    }
    let chart: Chart
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
