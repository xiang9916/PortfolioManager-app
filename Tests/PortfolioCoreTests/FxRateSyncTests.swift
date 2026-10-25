import XCTest
@testable import PortfolioCore

/// `FxRateSync`：成功/失败分组 + 落库口径（日期用 UTC 当日）。
@MainActor
final class FxRateSyncTests: XCTestCase {

    private struct Boom: Error {}

    func testWritesSuccessAndReportsFailures() async throws {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("pm-fx-" + UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let db = try Database(path: dir.appendingPathComponent("t.db").path)
        let now = Date(timeIntervalSince1970: 1_767_225_600) // 2026-01-01T00:00:00Z

        let report = await FxRateSync.run(currencies: ["HKD", "JPY", "USD"], now: now, into: db) { ccy in
            switch ccy {
            case "USD": return 7.1
            case "HKD": return nil
            default: throw Boom()
            }
        }

        XCTAssertEqual(report.updated, ["USD"])
        XCTAssertEqual(report.failed.sorted(), ["HKD", "JPY"])
        let rates = try db.fetchFxRates()
        XCTAssertEqual(rates.count, 1)
        XCTAssertEqual(rates.first?.currency, "USD")
        XCTAssertEqual(rates.first?.rateToCny, 7.1)
        XCTAssertEqual(rates.first?.source, "yahoo")
        XCTAssertEqual(rates.first?.asOfDate, SyncDates.utcDay(now))
    }

    func testEmptyCurrenciesDoNothing() async throws {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("pm-fx-" + UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let db = try Database(path: dir.appendingPathComponent("t.db").path)

        let report = await FxRateSync.run(currencies: [], now: Date(), into: db) { _ in 1.0 }

        XCTAssertEqual(report.updated, [])
        XCTAssertEqual(report.failed, [])
        XCTAssertTrue(try db.fetchFxRates().isEmpty)
    }
}
