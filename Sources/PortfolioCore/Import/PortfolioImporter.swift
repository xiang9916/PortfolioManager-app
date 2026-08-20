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

/// Maps a .numbers table name to (asset class, pool).
public enum AssetClassMapper {
    public static func classify(table: String?) -> (assetClass: String, pool: Pool) {
        guard let t = table else { return ("other", .overseas) }
        if t.contains("美国权益") || t.contains("美股") || t.contains("缓冲") { return ("us_equity", .overseas) }
        if t.contains("大中华权益") { return ("greater_cn_equity", .cross) }
        if t.contains("大中华固定收益") { return ("cn_fixed_income", .domestic) }
        if t.contains("黄金") { return ("gold", .cross) }
        if t.contains("比特币") { return ("btc", .overseas) }
        if t.contains("日本权益") { return ("jp_equity", .overseas) }
        if t.contains("新加坡权益") { return ("sg_equity", .overseas) }
        if t.contains("美国固定收益") { return ("us_fixed_income", .overseas) }
        if t.contains("REIT") { return ("us_reit", .overseas) }
        if t.contains("石油") || t.contains("能源") { return ("energy", .overseas) }
        return ("other", .overseas)
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
            let (cls, pool) = AssetClassMapper.classify(table: h.table)
            assets.append(Asset(key: h.ticker, name: h.name, ticker: h.ticker,
                                assetClass: cls, pool: pool,
                                currency: h.currency ?? "CNY", source: "numbers"))
            // .numbers 提取的 valueCny 已是人民币, 持仓币种记为 CNY (可之后在界面切换为标的币种并重填)
            holdings.append(Holding(assetKey: h.ticker, quantity: 0, costBasis: 0,
                                    value: h.valueCny, currency: "CNY", asOfDate: asOfDate))
        }
        try db.upsertAssets(assets)
        try db.upsertHoldings(holdings)
        try db.insertSnapshot(Snapshot(date: asOfDate, totalValue: ex.totalValue,
                                       domesticValue: ex.domesticValue, overseasValue: ex.overseasValue))
    }
}
