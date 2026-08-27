import XCTest
@testable import PortfolioCore

/// Regression tests for JSON backup import (能力3).
///
/// Root bug fixed here: Database.upsertAssets bound NULL for a nil sortOrder,
/// but assets.sort_order is NOT NULL — SQLite DEFAULT 0 only applies when the
/// column is omitted from the INSERT, not when NULL is bound explicitly. Every
/// JSON backup import into an empty DB died with
/// "NOT NULL constraint failed: assets.sort_order".
final class BackupImportTests: XCTestCase {
    private var tmpDir: URL!

    override func setUpWithError() throws {
        tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("pm-backup-tests-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: tmpDir, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: tmpDir)
    }

    private func makeDB(_ name: String = "test.db") throws -> Database {
        try Database(path: tmpDir.appendingPathComponent(name).path)
    }

    /// The exact failing path: assets with nil sortOrder (JSON backups exported
    /// before sort_order existed carry no sort_order field) must insert cleanly.
    func testUpsertAssetsWithNilSortOrderDoesNotThrow() throws {
        let db = try makeDB()
        try db.upsertAssets([
            Asset(key: "000307", name: "易方达黄金ETF联接A", ticker: "000307",
                  assetClass: "gold", pool: .domestic, currency: "CNY"),
            Asset(key: "GOOG", name: "谷歌", ticker: "GOOG",
                  assetClass: "us_equity", pool: .overseas, currency: "USD"),
        ])
        let rows = try db.fetchAssets()
        XCTAssertEqual(rows.count, 2)
        // sort_order 0 = unsorted → falls back to key order
        XCTAssertEqual(rows.map(\.key), ["000307", "GOOG"])
    }

    /// Legacy backup JSON (the shape of a real user file exported by an older
    /// build: no sort_order/market/source/fee_rate fields) must import fully.
    func testImportLegacyJSONWithoutSortOrder() throws {
        let fixture = tmpDir.appendingPathComponent("legacy.json")
        try """
        {
          "assets" : [
            {"asset_class" : "gold", "currency" : "CNY", "key" : "000307", "name" : "易方达黄金ETF联接A", "pool" : "domestic", "ticker" : "000307"},
            {"asset_class" : "us_equity", "currency" : "USD", "key" : "GOOG", "name" : "谷歌", "pool" : "overseas", "ticker" : "GOOG"}
          ],
          "holdings" : [
            {"asset_key" : "000307", "quantity" : 1000, "cost_basis" : 5.1, "currency" : "CNY", "as_of_date" : "2025-08-24"}
          ],
          "snapshots" : [],
          "income_periods" : [],
          "fx_rates" : [
            {"currency" : "USD", "rate_to_cny" : 7.12, "as_of_date" : "2025-08-24", "source" : "test"}
          ],
          "quotes" : [
            {"asset_key" : "000307", "price" : 5.2, "date" : "2025-08-24", "currency" : "CNY", "source" : "eastmoney"}
          ],
          "schema_version" : 6
        }
        """.write(to: fixture, atomically: true, encoding: .utf8)

        let db = try makeDB()
        try BackupManager(db: db, backupDir: tmpDir).importJSON(from: fixture)

        XCTAssertEqual(try db.fetchAssets().count, 2)
        XCTAssertEqual(try db.fetchHoldings().count, 1)
        XCTAssertEqual(try db.fetchFxRates().first?.rateToCny ?? 0, 7.12, accuracy: 1e-9)
        XCTAssertEqual(try db.fetchLatestQuotes()["000307"]?.price ?? 0, 5.2, accuracy: 1e-9)
    }

    /// exportJSON → importJSON roundtrip must preserve sort_order and the
    /// other asset metadata (market/source/fee_rate).
    func testExportImportRoundtripPreservesMetadata() throws {
        let db = try makeDB("export.db")
        try db.upsertAssets([
            Asset(key: "B", name: "second", pool: .domestic, currency: "CNY",
                  source: "numbers", feeRate: 0.0012, sortOrder: 2),
            Asset(key: "A", name: "first", ticker: "A", market: "US",
                  assetClass: "us_equity", pool: .overseas, currency: "USD", sortOrder: 1),
        ])
        let jsonURL = tmpDir.appendingPathComponent("backup.json")
        try BackupManager(db: db, backupDir: tmpDir).exportJSON(to: jsonURL)

        let restored = try makeDB("restore.db")
        try BackupManager(db: restored, backupDir: tmpDir).importJSON(from: jsonURL)

        let rows = try restored.fetchAssets()
        XCTAssertEqual(rows.map(\.key), ["A", "B"], "manual sort_order must survive the roundtrip")
        XCTAssertEqual(rows.first?.sortOrder, 1)
        let b = rows.first { $0.key == "B" }
        XCTAssertEqual(b?.source, "numbers")
        XCTAssertEqual(b?.feeRate ?? 0, 0.0012, accuracy: 1e-9)
    }

    /// A failed transaction must roll back AND leave the connection usable —
    /// the pre-fix code left BEGIN uncommitted, so every later write on the
    /// running app failed with "cannot start a transaction within a transaction".
    func testFailedTransactionRollsBackAndStaysUsable() throws {
        let db = try makeDB()
        XCTAssertThrowsError(try db.inTransaction {
            try db.exec("INSERT INTO assets(key, name, pool, currency) VALUES('A', 'a', 'domestic', 'CNY')")
            try db.exec("INSERT INTO no_such_table VALUES(1)")
        })
        // Rollback happened...
        XCTAssertEqual(try db.fetchAssets().count, 0)
        // ...and the connection is not stuck inside an open transaction.
        try db.upsertAssets([Asset(key: "B", name: "b", pool: .domestic, currency: "CNY")])
        XCTAssertEqual(try db.fetchAssets().map(\.key), ["B"])
    }

    /// quarterly_reports (财务分析重做) must survive the export → import roundtrip,
    /// including NULL manual fields (not yet filled quarters).
    func testExportImportRoundtripQuarterlyReports() throws {
        let db = try makeDB("q-export.db")
        try db.upsertQuarterlyReports([
            QuarterlyReport(periodEnd: "2026-03-31", marketValue: 10000, totalCost: 10000),
            QuarterlyReport(periodEnd: "2026-06-30", marketValue: 10500, totalCost: 10200,
                            interestDomestic: 100, taxes: 6),
        ])
        let jsonURL = tmpDir.appendingPathComponent("q-backup.json")
        try BackupManager(db: db, backupDir: tmpDir).exportJSON(to: jsonURL)

        let restored = try makeDB("q-restore.db")
        try BackupManager(db: restored, backupDir: tmpDir).importJSON(from: jsonURL)
        let rows = try restored.fetchQuarterlyReports()
        XCTAssertEqual(rows.map(\.periodEnd), ["2026-03-31", "2026-06-30"])
        XCTAssertEqual(rows[0].marketValue ?? 0, 10000, accuracy: 1e-9)
        XCTAssertNil(rows[0].interestDomestic, "未填写字段必须保持 NULL")
        XCTAssertEqual(rows[1].interestDomestic ?? 0, 100, accuracy: 1e-9)
        XCTAssertEqual(rows[1].taxes ?? 0, 6, accuracy: 1e-9)

        // 派生链在恢复后的库上照常工作.
        let computed = QuarterlyMetrics.compute(rows)
        XCTAssertEqual(computed[1].periodDays, 90)
        XCTAssertEqual(computed[1].cumInterest, 100)
    }

    /// Optional manual verification against a real user export:
    /// PM_BACKUP_JSON=~/Downloads/PortfolioBackup.json swift test --filter RealUser
    func testImportRealUserBackupFile() throws {
        guard let path = ProcessInfo.processInfo.environment["PM_BACKUP_JSON"] else {
            throw XCTSkip("set PM_BACKUP_JSON to a real backup file to run this")
        }
        let db = try makeDB()
        try BackupManager(db: db, backupDir: tmpDir).importJSON(from: URL(fileURLWithPath: path))
        let assets = try db.fetchAssets()
        XCTAssertGreaterThan(assets.count, 0)
        print("real backup imported: " + String(assets.count) + " assets, "
              + String(try db.fetchHoldings().count) + " holdings")
    }
}
