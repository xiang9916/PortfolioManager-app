import XCTest
@testable import PortfolioCore

/// `MarketDataSync` 的行为测试（假数据源 + 临时库，不联网）。
///
/// 改造前这套「并发限流 → 串行批量落库 → 股息缓存」只能在 App 里跑，
/// 既有测试不得不把它照抄一遍（`LiveDividendPipelineTests`）；现在直接测 module。
@MainActor
final class MarketDataSyncTests: XCTestCase {

    private var dir: URL!
    private var db: Database!

    override func setUp() async throws {
        dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("pm-sync-" + UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        db = try Database(path: dir.appendingPathComponent("t.db").path)
    }

    override func tearDown() async throws {
        db = nil
        try? FileManager.default.removeItem(at: dir)
    }

    // MARK: - 写入

    func testWritesPricesAndQuoteWithRawFields() async throws {
        var source = FakeSource()
        source.history = [PricePoint(assetKey: "SYM-A", date: "2026-01-02", close: 10.4,
                                     rawClose: 9.9, currency: "USD",
                                     open: 10, high: 11, low: 9.5)]
        source.quote = Quote(symbol: "SYM-A", price: 10.5, currency: "USD",
                             date: "2026-01-02", source: "fake")

        let report = await MarketDataSync.run(plan([target("A", source)]), into: db)

        XCTAssertEqual(report.pricePointCount, 1)
        XCTAssertEqual(report.quoteCount, 1)
        XCTAssertEqual(report.pointCounts["A"], 1)
        XCTAssertEqual(report.failedAssetKeys, [])

        let point = try XCTUnwrap(try db.fetchPrices(assetKey: "A").first)
        XCTAssertEqual(point.close, 10.4)
        XCTAssertEqual(point.rawClose, 9.9)
        XCTAssertEqual(point.open, 10)
        XCTAssertEqual(point.high, 11)
        XCTAssertEqual(point.low, 9.5)
        XCTAssertEqual(try db.fetchLatestQuotes()["A"]?.price, 10.5)
    }

    func testSkipsDividendsWhenFlagged() async throws {
        var source = FakeSource()
        source.history = [point("SYM-A")]
        source.dividendOutcome = .failed   // 被调用就会写入 failed meta
        let flagged = SyncPlan.Target(assetKey: "A", source: source,
                                      symbol: "SYM-A", skipDividends: true)

        let report = await MarketDataSync.run(plan([flagged]), into: db)

        XCTAssertEqual(report.dividendUpdateCount, 0)
        XCTAssertTrue(try db.fetchDividendFetchMetas().isEmpty)
    }

    func testWritesDividendsAndMetaOnOK() async throws {
        var source = FakeSource()
        source.history = [point("SYM-A")]
        source.dividends = [DividendEvent(exDate: "2025-06-01", amount: 0.5, currency: "USD")]

        let report = await MarketDataSync.run(plan([target("A", source)]), into: db)

        XCTAssertEqual(report.dividendUpdateCount, 1)
        let records = try db.fetchDividends()
        XCTAssertEqual(records.count, 1)
        XCTAssertEqual(records.first?.assetKey, "A")
        XCTAssertEqual(records.first?.source, "fake")
        let meta = try XCTUnwrap(try db.fetchDividendFetchMetas()["A"])
        XCTAssertEqual(meta.status, .ok)
        XCTAssertEqual(meta.source, "fake")
        XCTAssertFalse(meta.fetchedAt.isEmpty)
    }

    func testDividendUnavailableAndFailedKeepNullState() async throws {
        var unavailable = FakeSource()
        unavailable.history = [point("SYM-A")]
        unavailable.dividendOutcome = .unavailable
        var failed = FakeSource()
        failed.history = [point("SYM-B")]
        failed.dividendOutcome = .failed

        let report = await MarketDataSync.run(
            plan([target("A", unavailable), target("B", failed)]), into: db)

        XCTAssertEqual(report.dividendUpdateCount, 2, "两种 NULL 态都要写 meta")
        XCTAssertTrue(try db.fetchDividends().isEmpty, "NULL 态不写逐笔记录")
        let metas = try db.fetchDividendFetchMetas()
        XCTAssertEqual(metas["A"]?.status, .unavailable)
        XCTAssertEqual(metas["B"]?.status, .failed)
    }

    func testPriceFailureCountedButOthersStillWritten() async throws {
        var broken = FakeSource()
        broken.failHistory = true
        var healthy = FakeSource()
        healthy.history = [point("SYM-B")]

        let report = await MarketDataSync.run(
            plan([target("A", broken), target("B", healthy)]), into: db)

        XCTAssertEqual(report.failedAssetKeys, ["A"])
        XCTAssertEqual(report.pricePointCount, 1)
        XCTAssertTrue(try db.fetchPrices(assetKey: "A").isEmpty)
        XCTAssertEqual(try db.fetchPrices(assetKey: "B").count, 1)
    }

    func testEmptyPlanIsNoop() async {
        let report = await MarketDataSync.run(plan([]), into: db)
        XCTAssertEqual(report.pricePointCount, 0)
        XCTAssertEqual(report.quoteCount, 0)
        XCTAssertEqual(report.failedAssetKeys, [])
    }

    // MARK: - helpers

    private func plan(_ targets: [SyncPlan.Target], now: Date = Date()) -> SyncPlan {
        SyncPlan(targets: targets, dividendYears: [2025], now: now)
    }

    private func target(_ key: String, _ source: FakeSource) -> SyncPlan.Target {
        SyncPlan.Target(assetKey: key, source: source, symbol: "SYM-" + key, skipDividends: false)
    }

    private func point(_ symbol: String) -> PricePoint {
        PricePoint(assetKey: symbol.replacingOccurrences(of: "SYM-", with: ""),
                   date: "2026-01-02", close: 1.0, currency: "USD")
    }
}

/// 股息缓存新鲜度（7 天 + 同一自然年）。
final class DividendFreshnessTests: XCTestCase {

    private let calendar: Calendar = {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "UTC")!
        return c
    }()

    func testFreshWithinSevenDaysOfSameYear() {
        let meta = meta(fetchedAt: "2026-03-08 12:00:00")
        XCTAssertTrue(MarketDataSync.cacheIsFresh(meta, now: date("2026-03-10 12:00:00"),
                                                  calendar: calendar))
    }

    func testStaleAfterSevenDays() {
        let meta = meta(fetchedAt: "2026-03-01 12:00:00")
        XCTAssertFalse(MarketDataSync.cacheIsFresh(meta, now: date("2026-03-10 12:00:00"),
                                                   calendar: calendar))
    }

    func testStaleAcrossYearBoundary() {
        let meta = meta(fetchedAt: "2026-12-30 12:00:00")
        XCTAssertFalse(MarketDataSync.cacheIsFresh(meta, now: date("2027-01-02 12:00:00"),
                                                   calendar: calendar),
                       "跨年后目标年份变化，必须重抓（决策 Q24）")
    }

    func testUnparsableStampIsStale() {
        let meta = meta(fetchedAt: "not-a-date")
        XCTAssertFalse(MarketDataSync.cacheIsFresh(meta, now: date("2026-03-10 12:00:00"),
                                                   calendar: calendar))
    }

    private func meta(fetchedAt: String) -> DividendFetchMeta {
        DividendFetchMeta(assetKey: "A", status: .ok, source: "fake", fetchedAt: fetchedAt)
    }

    private func date(_ text: String) -> Date {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd HH:mm:ss"
        f.timeZone = TimeZone(identifier: "UTC")!
        return f.date(from: text)!
    }
}

// MARK: - 假数据源

private enum DividendOutcome: Sendable {
    case ok
    case unavailable
    case failed
}

private struct FakeSource: DataSource {
    let name = "fake"
    var history: [PricePoint] = []
    var quote: Quote?
    var dividends: [DividendEvent] = []
    var failHistory = false
    var dividendOutcome: DividendOutcome = .ok

    private struct Boom: Error {}

    func fetchHistory(symbol: String) async throws -> [PricePoint] {
        if failHistory { throw Boom() }
        return history
    }

    func fetchQuote(symbol: String) async throws -> Quote {
        if let quote { return quote }
        throw DataSourceError.empty(symbol)
    }

    func fetchDividends(symbol: String, years: [Int]) async throws -> [DividendEvent] {
        switch dividendOutcome {
        case .ok: return dividends
        case .unavailable: throw DataSourceError.empty(symbol)
        case .failed: throw Boom()
        }
    }
}
