import Foundation

public final class EastmoneySource: DataSource {
    public let name = "eastmoney"
    private let session: URLSession

    public init() {
        let config = URLSessionConfiguration.default
        config.httpAdditionalHeaders = ["User-Agent": "Mozilla/5.0"]
        self.session = URLSession(configuration: config)
    }

    public func fetchHistory(symbol: String) async throws -> [PricePoint] {
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
        let text = String(data: data, encoding: .utf8) ?? ""
        let rows = Self.parseNav(text)
        return rows.map { PricePoint(assetKey: symbol, date: $0.date, close: $0.value, currency: "CNY") }
    }

    /// Parse the pingzhongdata JS: prefer cumulative NAV (Data_ACWorthTrend), fall back to unit NAV.
    static func parseNav(_ text: String) -> [(date: String, value: Double)] {
        let markers = ["Data_ACWorthTrend", "Data_netWorthTrend"]
        var arrayText: String? = nil
        for m in markers {
            if let arr = extractJSArray(text, marker: m) {
                arrayText = arr
                break
            }
        }
        guard let arr = arrayText,
              let data = arr.data(using: .utf8),
              let parsed = try? JSONSerialization.jsonObject(with: data) as? [[Any]] else {
            return []
        }
        var out: [(date: String, value: Double)] = []
        for item in parsed {
            guard item.count >= 2 else { continue }
            let ts: Int64
            let val: Double
            if let t = item[0] as? Int64 { ts = t }
            else if let t = item[0] as? Int { ts = Int64(t) }
            else if let t = item[0] as? Double { ts = Int64(t) }
            else { continue }
            if let v = item[1] as? Double { val = v }
            else if let v = item[1] as? Int { val = Double(v) }
            else { continue }
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
