import XCTest
@testable import PortfolioCore

/// Schema v9/v10：prices 增加 open/high/low 与 raw_close（都可空）。
///
/// 三条约束：全新库直达最新版本且能存取这些列；v8/v9 旧库升级后**旧数据不丢、新列为 NULL**；
/// migrate() 可重复调用（幂等）。
final class SchemaMigrationTests: XCTestCase {
    private var tmpDir: URL!

    override func setUpWithError() throws {
        tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("pm-schema-tests-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: tmpDir, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: tmpDir)
    }

    private func path(_ name: String) -> String {
        tmpDir.appendingPathComponent(name).path
    }

    func testFreshDatabaseIsAtLatestVersionAndRoundTripsPriceColumns() throws {
        let db = try Database(path: path("fresh.db"))
        XCTAssertEqual(Schema.version, 10)
        XCTAssertEqual(db.currentSchemaVersion(), Schema.version)

        try db.upsertPrices([PricePoint(assetKey: "FICT", date: "2026-10-23", close: 3.0,
                                        rawClose: 300.0, open: 2.0, high: 4.0, low: 1.0)])
        let row = try XCTUnwrap(try db.fetchPrices(assetKey: "FICT").first)
        XCTAssertEqual(row.close, 3.0)
        XCTAssertEqual(row.rawClose, 300.0)
        XCTAssertEqual(row.open, 2.0)
        XCTAssertEqual(row.high, 4.0)
        XCTAssertEqual(row.low, 1.0)
    }

    /// v8 旧库（没有 v9 的三列、也没有 v10 的 raw_close）→ 升级到最新版本。
    func testUpgradeFromV8AddsNullableColumnsAndKeepsRows() throws {
        let file = path("upgrade.db")
        do {
            // 建一个最新版本的库，再手工退回「v8 形状」。
            let db = try Database(path: file)
            try db.upsertPrices([PricePoint(assetKey: "FICT", date: "2026-10-22", close: 12.5)])
            try db.exec("ALTER TABLE prices DROP COLUMN open;")
            try db.exec("ALTER TABLE prices DROP COLUMN high;")
            try db.exec("ALTER TABLE prices DROP COLUMN low;")
            try db.exec("ALTER TABLE prices DROP COLUMN raw_close;")
            try db.exec("UPDATE schema_meta SET value='8' WHERE key='version'")
        }

        let upgraded = try Database(path: file)   // 触发 v8 → v9 → v10
        XCTAssertEqual(upgraded.currentSchemaVersion(), Schema.version)

        let old = try XCTUnwrap(try upgraded.fetchPrices(assetKey: "FICT").first)
        XCTAssertEqual(old.close, 12.5)
        XCTAssertNil(old.open)
        XCTAssertNil(old.high)
        XCTAssertNil(old.low)
        XCTAssertNil(old.rawClose)

        try upgraded.upsertPrices([PricePoint(assetKey: "FICT", date: "2026-10-23", close: 13.0,
                                              rawClose: 1300.0,
                                              open: 12.4, high: 13.2, low: 12.3)])
        let updated = try XCTUnwrap(try upgraded.fetchPrices(assetKey: "FICT").last)
        XCTAssertEqual(updated.open ?? 0, 12.4, accuracy: 1e-12)
        XCTAssertEqual(updated.high ?? 0, 13.2, accuracy: 1e-12)
        XCTAssertEqual(updated.low ?? 0, 12.3, accuracy: 1e-12)
        XCTAssertEqual(updated.rawClose ?? 0, 1300.0, accuracy: 1e-12)
    }

    /// v9 旧库（有开/高/低、没有 raw_close）→ 只加一列，v9 的数据不动。
    func testUpgradeFromV9AddsRawCloseAndKeepsOHLC() throws {
        let file = path("upgrade-v9.db")
        do {
            let db = try Database(path: file)
            try db.upsertPrices([PricePoint(assetKey: "FICT", date: "2026-10-22", close: 12.5,
                                            open: 12.0, high: 13.0, low: 11.5)])
            try db.exec("ALTER TABLE prices DROP COLUMN raw_close;")
            try db.exec("UPDATE schema_meta SET value='9' WHERE key='version'")
        }

        let upgraded = try Database(path: file)   // 触发 v9 → v10
        XCTAssertEqual(upgraded.currentSchemaVersion(), Schema.version)

        let old = try XCTUnwrap(try upgraded.fetchPrices(assetKey: "FICT").first)
        XCTAssertEqual(old.close, 12.5)
        XCTAssertEqual(old.open ?? 0, 12.0, accuracy: 1e-12)
        XCTAssertEqual(old.high ?? 0, 13.0, accuracy: 1e-12)
        XCTAssertEqual(old.low ?? 0, 11.5, accuracy: 1e-12)
        XCTAssertNil(old.rawClose)   // 未回补 → 视图诚实退回百分比
    }

    func testMigrateIsIdempotent() throws {
        let db = try Database(path: path("idempotent.db"))
        try db.upsertPrices([PricePoint(assetKey: "FICT", date: "2026-10-23", close: 1.0)])
        try db.migrate()
        try db.migrate()
        XCTAssertEqual(db.currentSchemaVersion(), Schema.version)
        XCTAssertEqual(try db.fetchPrices(assetKey: "FICT").count, 1)
    }
}
