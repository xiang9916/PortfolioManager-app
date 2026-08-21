import Foundation

public final class EastmoneySource: DataSource, Sendable {
    public let name = "eastmoney"
    private let session: URLSession

    public init() {
        let config = URLSessionConfiguration.default
        config.httpAdditionalHeaders = ["User-Agent": "Mozilla/5.0"]
        self.session = URLSession(configuration: config)
    }

    /// Result of resolving a 6-digit fund code (能力1 天天基金联网校验).
    public struct FundInfo {
        public let code: String
        public let name: String?
    }

    /// Fetch the raw pingzhongdata JS text for a 6-digit fund code.
    private func fetchRaw(symbol: String) async throws -> String {
        // symbol = 6-digit fund code
        let urlStr = "https://fund.eastmoney.com/pingzhongdata/\(symbol).js"
        guard let url = URL(string: urlStr) else { throw DataSourceError.empty(symbol) }
        var req = URLRequest(url: url)
        req.setValue("Mozilla/5.0", forHTTPHeaderField: "User-Agent")
        req.setValue("https://fundf10.eastmoney.com/jjjz_\(symbol).html", forHTTPHeaderField: "Referer")
        let (data, response) = try await session.data(for: req)
        if let http = response as? HTTPURLResponse, http.statusCode != 200 {
            throw DataSourceError.http(http.statusCode, urlStr)
        }
        return String(data: data, encoding: .utf8) ?? ""
    }

    public func fetchHistory(symbol: String) async throws -> [PricePoint] {
        // History = cumulative NAV (累计净值), for 模拟历史财务表现 chart.
        let text = try await fetchRaw(symbol: symbol)
        let rows = Self.parseNav(text, cumulative: true)
        return rows.map { PricePoint(assetKey: symbol, date: $0.date, close: $0.value, currency: "CNY") }
    }

    /// Latest quote = unit NAV (单位净值, 每份实际价格), for market-value calculation.
    public func fetchQuote(symbol: String) async throws -> Quote {
        let text = try await fetchRaw(symbol: symbol)
        let rows = Self.parseNav(text, cumulative: false)
        guard let last = rows.last else { throw DataSourceError.empty(symbol) }
        return Quote(symbol: symbol, price: last.value, currency: "CNY",
                     date: last.date, source: name)
    }

    /// Validate a fund code and resolve its display name (fS_name). Throws when the fund is unknown.
    public func lookup(symbol: String) async throws -> FundInfo {
        let text = try await fetchRaw(symbol: symbol)
        let name = Self.extractName(text)
        guard name != nil || !Self.parseNav(text).isEmpty else {
            throw DataSourceError.empty(symbol)
        }
        return FundInfo(code: symbol, name: name)
    }

    /// Extract fS_name = "..." from the pingzhongdata JS.
    static func extractName(_ text: String) -> String? {
        guard let r = text.range(of: "fS_name") else { return nil }
        let after = text[r.upperBound...]
        guard let eq = after.firstIndex(of: "=") else { return nil }
        let afterEq = after[after.index(after: eq)...]
        guard let q = afterEq.firstIndex(of: "\"") else { return nil }
        let inner = afterEq[afterEq.index(after: q)...]
        guard let q2 = inner.firstIndex(of: "\"") else { return nil }
        return String(inner[..<q2])
    }

    /// Parse the pingzhongdata JS.
    /// - Parameter cumulative: true → 累计净值 (Data_ACWorthTrend, fallback to unit);
    ///   false → 单位净值 (Data_netWorthTrend only, the actual per-unit price for market value).
    static func parseNav(_ text: String, cumulative: Bool = true) -> [(date: String, value: Double)] {
        let markers = cumulative
            ? ["Data_ACWorthTrend", "Data_netWorthTrend"]
            : ["Data_netWorthTrend"]
        var arrayText: String? = nil
        for m in markers {
            if let arr = extractJSArray(text, marker: m) {
                arrayText = arr
                break
            }
        }
        guard let arr = arrayText,
              let data = arr.data(using: .utf8),
              let parsed = try? JSONSerialization.jsonObject(with: data) as? [Any] else {
            return []
        }
        var out: [(date: String, value: Double)] = []
        for item in parsed {
            let ts: Int64
            let val: Double
            if let pair = item as? [Any], pair.count >= 2 {
                // Data_ACWorthTrend format: [timestamp, value]
                if let t = pair[0] as? Int64 { ts = t }
                else if let t = pair[0] as? Int { ts = Int64(t) }
                else if let t = pair[0] as? Double { ts = Int64(t) }
                else { continue }
                if let v = pair[1] as? Double { val = v }
                else if let v = pair[1] as? Int { val = Double(v) }
                else { continue }
            } else if let dict = item as? [String: Any] {
                // Data_netWorthTrend format: {"x": timestamp, "y": value, ...}
                let xVal = dict["x"]
                let yVal = dict["y"]
                if let t = xVal as? Int64 { ts = t }
                else if let t = xVal as? Int { ts = Int64(t) }
                else if let t = xVal as? Double { ts = Int64(t) }
                else { continue }
                if let v = yVal as? Double { val = v }
                else if let v = yVal as? Int { val = Double(v) }
                else { continue }
            } else {
                continue
            }
            out.append((DateUtil.dateString(fromTimestampMillis: ts), val))
        }
        return out
    }

    /// Extract the JS array literal "var <marker> = [...] ;" as a JSON string.
    static func extractJSArray(_ text: String, marker: String) -> String? {
        guard let r = text.range(of: marker) else { return nil }
        let after = text[r.upperBound...]
        guard let ob = after.firstIndex(of: "[") else { return nil }
        let fromBracket = after[ob...]
        guard let semi = fromBracket.firstIndex(of: ";") else { return nil }
        var idx = text.index(before: semi)
        while idx > fromBracket.startIndex && text[idx] != "]" {
            idx = text.index(before: idx)
        }
        guard text[idx] == "]" else { return nil }
        return String(fromBracket[...idx])
    }
}
