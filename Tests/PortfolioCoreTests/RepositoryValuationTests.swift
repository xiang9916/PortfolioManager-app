import XCTest
@testable import PortfolioCore

/// Repository 与组合估值的接线测试（临时库）。
/// 锁定两条最关键的行为：基金缺 quote 时用单位净值；quote 的日期与价格成对使用。
final class RepositoryValuationTests: XCTestCase {
    private var tmpDir: URL!

    override func setUpWithError() throws {
        tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("pm-valuation-tests-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: tmpDir, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: tmpDir)
    }

    private func makeDB(_ name: String = "valuation.db") throws -> Database {
        try Database(path: tmpDir.appendingPathComponent(name).path)
    }

    private func fund(_ key: String = "F") -> Asset {
        Asset(key: key, name: "示例基金", ticker: "000000.CN_Fund", market: .cn,
              assetClass: "cn_fund", pool: .domestic, currency: "CNY")
    }

    private func stock(_ key: String = "S") -> Asset {
        Asset(key: key, name: "示例权益", ticker: "TEST", market: .us,
              assetClass: "us_equity", pool: .overseas, currency: "USD")
    }

    func testFundWithoutQuoteUsesUnitNAVNotCumulative() throws {
        let db = try makeDB()
        try db.upsertAssets([fund()])
        try db.upsertHoldings([Holding(assetKey: "F", quantity: 100, costBasis: 500,
                                       currency: "CNY", asOfDate: "2026-09-30")])
        // close = 累计净值 5.60，raw_close = 单位净值 5.54；没有 quote。
        try db.upsertPrices([PricePoint(assetKey: "F", date: "2026-09-30",
                                        close: 5.60, rawClose: 5.54, currency: "CNY")])

        let repo = Repository(db: db)
        let alloc = try repo.fetchAllocation()
        XCTAssertEqual(alloc.totalValue, 554, accuracy: 1e-9, "市值必须用单位净值")
        XCTAssertEqual(alloc.totalCost, 500, accuracy: 1e-9)
        XCTAssertEqual(alloc.asOfDate, "2026-09-30")
        let row = try XCTUnwrap(repo.fetchAssetPerspectives().first)
        XCTAssertEqual(row.latestPrice ?? 0, 5.54, accuracy: 1e-9)
        XCTAssertEqual(row.latestDate, "2026-09-30")
    }

    func testQuoteDateIsUsedForLatestDate() throws {
        let db = try makeDB()
        try db.upsertAssets([stock()])
        try db.upsertHoldings([Holding(assetKey: "S", quantity: 2, costBasis: 100,
                                       currency: "USD", asOfDate: "2026-09-01")])
        try db.upsertFxRates([FxRate(currency: "USD", rateToCny: 7.0, asOfDate: "2026-10-05")])
        try db.upsertQuotes([Quote(symbol: "S", price: 10, currency: "USD",
                                   date: "2026-10-05", source: "test")])
        try db.upsertPrices([PricePoint(assetKey: "S", date: "2026-09-30",
                                        close: 9, rawClose: 9, currency: "USD")])

        let repo = Repository(db: db)
        let row = try XCTUnwrap(repo.fetchAssetPerspectives().first)
        XCTAssertEqual(row.latestPrice ?? 0, 10, accuracy: 1e-9)
        XCTAssertEqual(row.latestDate, "2026-10-05", "日期必须跟价格同一个时点")
        XCTAssertEqual(row.valueCny, 140, accuracy: 1e-9)   // 2 × 10 × 7
    }

    func testQuoteRemainsAuthoritativeOverHistory() throws {
        let db = try makeDB()
        try db.upsertAssets([stock()])
        try db.upsertHoldings([Holding(assetKey: "S", quantity: 2, costBasis: 100,
                                       currency: "USD", asOfDate: "2026-09-01")])
        try db.upsertFxRates([FxRate(currency: "USD", rateToCny: 7.0, asOfDate: "2026-10-05")])
        try db.upsertQuotes([Quote(symbol: "S", price: 10, currency: "USD",
                                   date: "2026-10-05", source: "test")])
        try db.upsertPrices([PricePoint(assetKey: "S", date: "2026-10-05",
                                        close: 9, rawClose: 9, currency: "USD")])

        let alloc = try Repository(db: db).fetchAllocation()
        XCTAssertEqual(alloc.totalValue, 140, accuracy: 1e-9)   // 用 quote 10，不是 9
    }

    func testAllocationAsOfDateIsLatestPriceDate() throws {
        let db = try makeDB()
        try db.upsertAssets([fund("F"), stock("S")])
        try db.upsertHoldings([
            Holding(assetKey: "F", quantity: 1, costBasis: 1, currency: "CNY", asOfDate: "2026-09-01"),
            Holding(assetKey: "S", quantity: 1, costBasis: 1, currency: "USD", asOfDate: "2026-09-01"),
        ])
        try db.upsertQuotes([
            Quote(symbol: "F", price: 1, currency: "CNY", date: "2026-09-01", source: "test"),
            Quote(symbol: "S", price: 1, currency: "USD", date: "2026-10-01", source: "test"),
        ])
        let alloc = try Repository(db: db).fetchAllocation()
        XCTAssertEqual(alloc.asOfDate, "2026-10-01")
    }
}
