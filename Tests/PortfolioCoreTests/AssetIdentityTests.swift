import XCTest
@testable import PortfolioCore

/// 资产身份的单一入口：代码规范化 / 市场 / 池 / 取数源。
///
/// 锁定 ADR 0010：
/// - 基金后缀大小写不敏感，.SS 是 A 股后缀不是基金；
/// - 市场：显式 market → ticker → key；池：市场 → 币种回退；
/// - 取数：基金代码 → 目录 key → Yahoo；
/// - 删除标的必须连 quote 一起删。
final class AssetIdentityTests: XCTestCase {
    private var tmpDir: URL!

    override func setUpWithError() throws {
        tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("pm-identity-tests-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: tmpDir, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: tmpDir)
    }

    private func makeDB(_ name: String = "identity.db") throws -> Database {
        try Database(path: tmpDir.appendingPathComponent(name).path)
    }

    // MARK: - 代码规范化

    func testCanonicalTrimsAndUppercases() {
        XCTAssertEqual(AssetTicker.canonical(" test "), "TEST")
        XCTAssertEqual(AssetTicker.canonical(nil), "")
        XCTAssertEqual(AssetTicker.canonical("   "), "")
    }

    func testFundCodeAcceptsCaseInsensitiveSuffix() {
        let inputs = ["123456", "123456.CN_Fund", "123456.CN_FUND", "123456.cn_fund",
                      " 123456.sz ", "123456.SH"]
        for raw in inputs {
            XCTAssertEqual(AssetTicker.fundCode(raw), "123456", raw)
            XCTAssertEqual(AssetTicker.canonical(raw), "123456", raw)
        }
    }

    func testFundCodeRejectsAShareAndNonSixDigit() {
        XCTAssertNil(AssetTicker.fundCode("654321.SS"))
        XCTAssertEqual(AssetTicker.canonical("654321.SS"), "654321.SS")
        XCTAssertNil(AssetTicker.fundCode("ABCD"))
        XCTAssertNil(AssetTicker.fundCode("ABCD.SZ"))
        XCTAssertEqual(AssetTicker.canonical("ABCD.SZ"), "ABCD.SZ")
        XCTAssertNil(AssetTicker.fundCode("ABCD.HK"))
    }

    // MARK: - 市场 / 池

    func testMarketPrefersExplicitThenTickerThenKey() {
        XCTAssertEqual(AssetIdentity(key: "X", ticker: "ABCD.HK", marketRaw: "US", currency: "USD").market, .us)
        XCTAssertEqual(AssetIdentity(key: "X", ticker: "ABCD.HK", marketRaw: nil, currency: "HKD").market, .hk)
        XCTAssertEqual(AssetIdentity(key: "123456", ticker: nil, marketRaw: nil, currency: "CNY").market, .cn)
    }

    func testEmptyTickerFallsBackToKey() {
        let identity = AssetIdentity(key: "O_IALT", ticker: "   ", marketRaw: nil, currency: "USD")
        XCTAssertEqual(identity.canonicalTicker, "O_IALT")
        XCTAssertEqual(identity.feed?.kind, .yahoo)
        XCTAssertEqual(identity.feed?.symbol, "IALT")
    }

    func testPoolUsesMarketThenCurrencyFallback() {
        XCTAssertEqual(AssetIdentity(key: "A", ticker: "TEST", marketRaw: nil, currency: "CNY").pool, .overseas)
        // key 不是可推断的代码形态（下划线），才落到币种回退
        XCTAssertEqual(AssetIdentity(key: "KEY_B", ticker: nil, marketRaw: "LSE", currency: "CNY").pool, .domestic)
        XCTAssertEqual(AssetIdentity(key: "KEY_C", ticker: nil, marketRaw: "LSE", currency: "GBP").pool, .overseas)
        XCTAssertEqual(AssetIdentity(key: "D", ticker: "123456", marketRaw: nil, currency: "USD").pool, .domestic)
    }

    // MARK: - 取数源

    func testFeedPrefersFundCodeOverCatalogKey() {
        let feed = AssetFeedResolver.feed(key: "O_IALT", ticker: "123456.CN_FUND")
        XCTAssertEqual(feed?.kind, .fund)
        XCTAssertEqual(feed?.symbol, "123456")
    }

    func testFeedResolvesModeledCatalogKeys() {
        let expected: [(String, SourceKind)] = [
            ("D_CN_QDII_STOCK", .fund),
            ("D_CN_QDII_STABLE", .fund),
            ("D_CN_QDII_COMMODITY", .yahoo),
            ("D_GREATER_CN", .yahoo),
        ]
        for (key, kind) in expected {
            let feed = AssetFeedResolver.feed(key: key, ticker: nil)
            XCTAssertEqual(feed?.kind, kind, key)
            XCTAssertFalse(feed?.symbol.isEmpty ?? true, key)
        }
    }

    func testFeedFallsBackToYahooAndNil() {
        XCTAssertEqual(AssetFeedResolver.feed(key: "TEST", ticker: nil)?.kind, .yahoo)
        XCTAssertEqual(AssetFeedResolver.feed(key: "TEST", ticker: nil)?.symbol, "TEST")
        XCTAssertNil(AssetFeedResolver.feed(key: "", ticker: nil))
    }

    func testFundCodeFromKeyWhenTickerMissing() {
        XCTAssertEqual(AssetFeedResolver.feed(key: "123456", ticker: nil)?.kind, .fund)
        XCTAssertEqual(AssetFeedResolver.feed(key: "123456.CN_FUND", ticker: nil)?.symbol, "123456")
    }

    // MARK: - 归一化判定

    func testMarketNeedsRewriteConverges() {
        XCTAssertTrue(AssetIdentity.marketNeedsRewrite(stored: "us", resolved: .us))
        XCTAssertTrue(AssetIdentity.marketNeedsRewrite(stored: " US ", resolved: .us))
        XCTAssertTrue(AssetIdentity.marketNeedsRewrite(stored: "NASDAQ", resolved: .us))
        XCTAssertFalse(AssetIdentity.marketNeedsRewrite(stored: "US", resolved: .us))
        XCTAssertTrue(AssetIdentity.marketNeedsRewrite(stored: "LSE", resolved: nil))
        XCTAssertTrue(AssetIdentity.marketNeedsRewrite(stored: "", resolved: nil))
        XCTAssertFalse(AssetIdentity.marketNeedsRewrite(stored: nil, resolved: nil))
        XCTAssertTrue(AssetIdentity.marketNeedsRewrite(stored: nil, resolved: .cn))
    }

    // MARK: - 生命周期

    func testDeleteAssetRemovesQuoteAndReaddIsClean() throws {
        let db = try makeDB()
        try db.upsertAssets([Asset(key: "TEST", name: "示例", ticker: "TEST",
                                   pool: .overseas, currency: "USD")])
        try db.upsertQuotes([Quote(symbol: "TEST", price: 10, currency: "USD",
                                   date: "2026-10-01", source: "test")])
        XCTAssertEqual(try db.fetchLatestQuotes()["TEST"]?.price ?? 0, 10, accuracy: 1e-9)

        try db.deleteAsset(key: "TEST")
        XCTAssertTrue(try db.fetchLatestQuotes().isEmpty, "删除标的必须连 quote 一起删")

        try db.upsertAssets([Asset(key: "TEST", name: "示例", ticker: "TEST",
                                   pool: .overseas, currency: "USD")])
        try db.upsertHoldings([Holding(assetKey: "TEST", quantity: 1, costBasis: 1,
                                       currency: "USD", asOfDate: "2026-10-01")])
        let valuation = try Repository(db: db).loadValuation()
        XCTAssertEqual(valuation.positions.first?.priceKind, .missing, "重建后不得复活旧报价")
    }
}
