import Foundation

// Structures matching the enhanced extract_portfolio.py output.
struct ExtractResult: Codable {
    let sourceFile: String
    let poolMode: String
    let domesticValue: Double
    let overseasValue: Double
    let totalValue: Double
    let usEquity: USEquity?
    let holdings: [ExtractedHolding]
    let warnings: [String]
}

struct USEquity: Codable {
    let totalValue: Double
    let holdings: [ExtractedHolding]
}

struct ExtractedHolding: Codable {
    let ticker: String
    let name: String
    let currency: String?
    let valueCny: Double
    let table: String?
    let weight: Double?
}

/// Maps a .numbers table name to an **asset class**.
/// 池归属不再由表名硬编码决定：它由 `AssetMarket`（market → 池）派生，
/// 旧的 `.cross` 第三态已废除（见 `docs/adr/0002-remove-cross-pool.md`）。
/// 注意「大中华权益」「黄金」两张表是**混币种**的（A股基金 + 港股），
/// 按 ticker 推断市场后天然拆到境内与境外两侧。
public enum AssetClassMapper {
    public static func classify(table: String?) -> String {
        guard let t = table else { return "other" }
        if t.contains("美国权益") || t.contains("美股") || t.contains("缓冲") { return "us_equity" }
        if t.contains("大中华权益") { return "greater_cn_equity" }
        if t.contains("大中华固定收益") { return "cn_fixed_income" }
        if t.contains("黄金") { return "gold" }
        if t.contains("比特币") { return "btc" }
        if t.contains("日本权益") { return "jp_equity" }
        if t.contains("新加坡权益") { return "sg_equity" }
        if t.contains("美国固定收益") { return "us_fixed_income" }
        if t.contains("REIT") { return "us_reit" }
        if t.contains("石油") || t.contains("能源") { return "energy" }
        return "other"
    }
}

/// Imports the extracted .numbers state into SQLite (assets + holdings + snapshot).
public enum PortfolioImporter {
    public static func importExtract(url: URL, into db: Database, asOfDate: String) throws {
        let data = try Data(contentsOf: url)
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let ex = try decoder.decode(ExtractResult.self, from: data)

        var assets: [Asset] = []
        var holdings: [Holding] = []
        for h in ex.holdings {
            let cls = AssetClassMapper.classify(table: h.table)
            let currency = h.currency ?? "CNY"
            // market / pool 不再来自表名：市场按 ticker 推断（提取 JSON 里根本没有 market 字段），
            // 池由市场派生，推断不出市场时按币种回退。
            let market = AssetMarket.infer(ticker: h.ticker)
            assets.append(Asset(key: h.ticker, name: h.name, ticker: h.ticker,
                                market: market, assetClass: cls,
                                pool: AssetMarket.pool(market: nil, ticker: h.ticker, currency: currency),
                                currency: currency, source: "numbers"))
            // 份额/成本留空(0), 由用户在资产明细手填; 市值 = 份额 × 最后价 (派生).
            holdings.append(Holding(assetKey: h.ticker, quantity: 0, costBasis: 0,
                                    currency: currency, asOfDate: asOfDate))
        }
        try db.upsertAssets(assets)
        try db.upsertHoldings(holdings)
        try db.insertSnapshot(Snapshot(date: asOfDate, totalValue: ex.totalValue,
                                       domesticValue: ex.domesticValue, overseasValue: ex.overseasValue))
    }
}
