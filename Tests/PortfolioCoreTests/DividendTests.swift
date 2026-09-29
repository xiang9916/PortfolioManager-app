import XCTest
@testable import PortfolioCore

/// 全资产股息 / 全资产股息率 的口径测试。
///
/// 锁定这些决策：按除息日归入自然年 (Q18)；今年预计 = max(0, 2×去年 − 前年) (Q5)；
/// 原币税前、折人民币税后 (Q35)；市场税率 US 10% / HK 28% / A股 0% (Q4/Q36/Q38)；
/// 三态 ok/unavailable/failed，NULL 态在汇总里按 0 计但覆盖率只统计非 NULL (Q28/Q12)；
/// 缺汇率不静默按 1.0 (Q14)。
final class DividendTests: XCTestCase {
    private var tmpDir: URL!

    override func setUpWithError() throws {
        tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("pm-dividend-tests-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: tmpDir, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: tmpDir)
    }

    private func makeDB(_ name: String = "div.db") throws -> Database {
        try Database(path: tmpDir.appendingPathComponent(name).path)
    }

    // MARK: - 税率

    func testTaxRateMarketTakesPriorityOverCurrency() {
        // market 优先
        XCTAssertEqual(DividendTax.rate(market: "US", currency: "HKD"), DividendTax.usRate)
        XCTAssertEqual(DividendTax.rate(market: "HK", currency: "USD"), DividendTax.hkRate)
        XCTAssertEqual(DividendTax.rate(market: "SH", currency: "USD"), DividendTax.cnRate)
        XCTAssertEqual(DividendTax.rate(market: "nasdaq", currency: "CNY"), DividendTax.usRate)
        // market 为空/未识别 → 回退 currency
        XCTAssertEqual(DividendTax.rate(market: nil, currency: "USD"), DividendTax.usRate)
        XCTAssertEqual(DividendTax.rate(market: "", currency: "HKD"), DividendTax.hkRate)
        XCTAssertEqual(DividendTax.rate(market: "  ", currency: "CNY"), DividendTax.cnRate)
        XCTAssertEqual(DividendTax.rate(market: "XX", currency: "USD"), DividendTax.usRate)
        // 都无法判定 → nil (未配置, 调用方按 0 计)
        XCTAssertNil(DividendTax.rate(market: nil, currency: "JPY"))
        XCTAssertNil(DividendTax.rate(market: "OTHER", currency: "SGD"))
        XCTAssertEqual(DividendTax.label(nil), "未配置")
        XCTAssertEqual(DividendTax.label(0.28), "28%")
    }

    // MARK: - 数据源解析

    func testEastmoneyDividendTableParsing() {
        let html = """
        <div><table class='w782 comm cfxq'><thead><tr><th class='first'>年份</th><th>权益登记日</th>\
        <th>除息日</th><th>每10份分红</th><th class='last'>分红发放日</th></tr></thead><tbody>\
        <tr><td>2025年</td><td>2025-12-15</td><td>2025-12-15</td><td>每10份派现金0.1100元</td><td>2025-12-16</td></tr>\
        <tr><td>2024年</td><td>2024-06-08</td><td>2024-06-08</td><td>每10份派现金0.1390元</td><td>2024-06-11</td></tr>\
        <tr><td>2023年</td><td>2023-01-01</td><td>2023-01-01</td><td>每10份送0.5份</td><td>2023-01-02</td></tr>\
        </tbody></table></div>
        """
        let events = EastmoneySource.parseDividendTable(html)
        // 送股行被跳过 (决策 Q21: 只算现金股息)
        XCTAssertEqual(events.count, 2)
        XCTAssertEqual(events.map(\.exDate), ["2024-06-08", "2025-12-15"])
        XCTAssertEqual(events[0].amount, 0.0139, accuracy: 1e-9)  // 每10份 ÷10
        XCTAssertEqual(events[1].amount, 0.011, accuracy: 1e-9)
        XCTAssertEqual(events[0].currency, "CNY")
    }

    func testEastmoneyNoDividendPageReturnsEmpty() {
        let html = "<html><body><div class='box'>暂无分红信息!</div></body></html>"
        XCTAssertTrue(EastmoneySource.parseDividendTable(html).isEmpty)
    }

    func testYahooYearWindowBoundary() {
        XCTAssertEqual(YahooFinanceSource.epochJan1(2024), 1_704_067_200)  // 2024-01-01 UTC
        XCTAssertEqual(YahooFinanceSource.epochJan1(2025), 1_735_689_600)
        XCTAssertEqual(YahooFinanceSource.epochJan1(2026) - YahooFinanceSource.epochJan1(2025),
                       365 * 86_400)
    }

    func testDividendRecordYearComesFromExDate() {
        let r = DividendRecord(assetKey: "A", exDate: "2024-12-31", amount: 1, currency: "USD", source: "t")
        XCTAssertEqual(r.year, 2024)
    }

    // MARK: - 聚合口径

    func testDividendSummaryAggregationAndThreeStates() throws {
        let db = try makeDB()
        let y = Calendar.current.component(.year, from: Date())
        let prevPrev = y - 2, prev = y - 1

        try db.upsertAssets([
            // 美股: market 空 → 按 currency USD → 10%
            Asset(key: "US_STOCK", name: "美股", pool: .overseas, currency: "USD"),
            // 港股: currency HKD → 28%
            Asset(key: "HK_STOCK", name: "港股", pool: .overseas, currency: "HKD"),
            // A股: currency CNY → 0%
            Asset(key: "CN_STOCK", name: "A股", pool: .domestic, currency: "CNY"),
            // 无覆盖 (NULL 态) 的标的
            Asset(key: "NEW_LISTING", name: "新上市", pool: .overseas, currency: "HKD"),
            // 降息导致外推为负 → 截断为 0
            Asset(key: "CUT", name: "降息股", pool: .overseas, currency: "USD"),
            // 缺汇率
            Asset(key: "NO_FX", name: "无汇率", pool: .overseas, currency: "JPY"),
        ])
        try db.upsertHoldings([
            Holding(assetKey: "US_STOCK", quantity: 100, costBasis: 1000, currency: "USD", asOfDate: "2026-09-29"),
            Holding(assetKey: "HK_STOCK", quantity: 100, costBasis: 1000, currency: "HKD", asOfDate: "2026-09-29"),
            Holding(assetKey: "CN_STOCK", quantity: 1000, costBasis: 1000, currency: "CNY", asOfDate: "2026-09-29"),
            Holding(assetKey: "NEW_LISTING", quantity: 100, costBasis: 1000, currency: "HKD", asOfDate: "2026-09-29"),
            Holding(assetKey: "CUT", quantity: 100, costBasis: 1000, currency: "USD", asOfDate: "2026-09-29"),
            Holding(assetKey: "NO_FX", quantity: 100, costBasis: 1000, currency: "JPY", asOfDate: "2026-09-29"),
        ])
        try db.upsertFxRates([
            FxRate(currency: "USD", rateToCny: 7.0, asOfDate: "2026-09-29", source: "test"),
            FxRate(currency: "HKD", rateToCny: 0.9, asOfDate: "2026-09-29", source: "test"),
        ])
        try db.replaceDividendsBatch([
            // 美股: 前年 1.0/股, 去年 1.2/股 → 今年预计 = max(0, 2.4−1.0) = 1.4/股 × 100 = 140 USD
            ("US_STOCK", [rec("US_STOCK", "\(prevPrev)-02-09", 1.0, "USD"),
                          rec("US_STOCK", "\(prev)-02-10", 1.2, "USD")],
             meta("US_STOCK", .ok, "yahoo")),
            // 港股: 前年 3.0, 去年 1.0 → 2×1−3 = −1 → 截断为 0
            ("HK_STOCK", [rec("HK_STOCK", "\(prevPrev)-05-11", 3.0, "HKD"),
                          rec("HK_STOCK", "\(prev)-05-12", 1.0, "HKD")],
             meta("HK_STOCK", .ok, "yahoo")),
            // A股: 一年两次派息, 按除息日归入自然年求和
            ("CN_STOCK", [rec("CN_STOCK", "\(prevPrev)-06-20", 0.3, "CNY"),
                          rec("CN_STOCK", "\(prevPrev)-11-20", 0.2, "CNY"),
                          rec("CN_STOCK", "\(prev)-06-20", 0.25, "CNY"),
                          rec("CN_STOCK", "\(prev)-11-20", 0.25, "CNY")],
             meta("CN_STOCK", .ok, "yahoo")),
            // 新上市: 数据源无覆盖 → NULL 态
            ("NEW_LISTING", [], meta("NEW_LISTING", .unavailable, "yahoo")),
            // 降息: 同上 HK 的负数情形, 用 USD 再验一次
            ("CUT", [rec("CUT", "\(prevPrev)-03-01", 2.0, "USD"),
                     rec("CUT", "\(prev)-03-01", 0.5, "USD")],
             meta("CUT", .ok, "yahoo")),
            // 缺汇率 (JPY 不在 fx_rates, 且 JPY 无税率配置 → 0%)
            ("NO_FX", [rec("NO_FX", "\(prev)-04-01", 10.0, "JPY")], meta("NO_FX", .ok, "yahoo")),
        ])

        let repo = Repository(db: db)
        let summary = try repo.fetchDividendSummary()
        // 美股: 1.4 × 100 × 7.0 × (1−0.10) = 882
        // 港股: 0 (截断)
        // A股: 去年合计 0.5/股 → (2×0.5 − 0.5) = 0.5/股 × 1000 × 1.0 × 1.0 = 500
        // 新上市: NULL → 0; 降息: 0; 缺汇率: 0
        XCTAssertEqual(summary.totalNetCny, 1382, accuracy: 1e-6)
        XCTAssertEqual(summary.assetCount, 6)
        // 覆盖率只统计非 NULL 态 (5 个 ok, 1 个 unavailable)
        XCTAssertEqual(summary.coveredCount, 5)
        XCTAssertNotNil(summary.fetchedAt)

        let rows = try repo.fetchAssetPerspectives()
        let us = try XCTUnwrap(rows.first { $0.assetKey == "US_STOCK" }?.dividend)
        XCTAssertEqual(us.status, .ok)
        XCTAssertEqual(us.perSharePrevPrev, 1.0, accuracy: 1e-9)
        XCTAssertEqual(us.perSharePrev, 1.2, accuracy: 1e-9)
        XCTAssertEqual(us.prevPrevTotal ?? 0, 100, accuracy: 1e-9)
        XCTAssertEqual(us.prevTotal ?? 0, 120, accuracy: 1e-9)
        XCTAssertEqual(us.estimatedTotal ?? 0, 140, accuracy: 1e-9)   // 原币, 税前
        XCTAssertEqual(us.estimatedTotalCny ?? 0, 882, accuracy: 1e-6) // 税后
        XCTAssertEqual(us.taxRate ?? -1, 0.10, accuracy: 1e-9)
        XCTAssertFalse(us.fxMissing)

        let hk = try XCTUnwrap(rows.first { $0.assetKey == "HK_STOCK" }?.dividend)
        XCTAssertEqual(hk.estimatedTotal ?? -1, 0, accuracy: 1e-9)   // 负值截断为 0
        XCTAssertEqual(hk.taxRate ?? -1, 0.28, accuracy: 1e-9)

        let cn = try XCTUnwrap(rows.first { $0.assetKey == "CN_STOCK" }?.dividend)
        XCTAssertEqual(cn.perSharePrevPrev, 0.5, accuracy: 1e-9)     // 0.3 + 0.2 按年求和
        XCTAssertEqual(cn.perSharePrev, 0.5, accuracy: 1e-9)
        XCTAssertEqual(cn.estimatedTotal ?? 0, 500, accuracy: 1e-6)  // (2×0.5−0.5)×1000
        XCTAssertEqual(cn.taxRate ?? -1, 0.0, accuracy: 1e-9)

        // NULL 态: 四个金额字段全 nil (详情显示「—」)
        let nl = try XCTUnwrap(rows.first { $0.assetKey == "NEW_LISTING" }?.dividend)
        XCTAssertEqual(nl.status, .unavailable)
        XCTAssertNil(nl.prevTotal)
        XCTAssertNil(nl.estimatedTotal)
        XCTAssertNil(nl.estimatedTotalCny)

        // 缺汇率: 原币有值, 折人民币 nil 且 fxMissing
        let noFx = try XCTUnwrap(rows.first { $0.assetKey == "NO_FX" }?.dividend)
        XCTAssertTrue(noFx.fxMissing)
        XCTAssertNil(noFx.estimatedTotalCny)
        XCTAssertNotNil(noFx.estimatedTotal)
        XCTAssertNil(noFx.taxRate)   // JPY 未配置税率
    }

    func testCurrentYearActualDividendIsTracked() throws {
        let db = try makeDB("ytd.db")
        let y = Calendar.current.component(.year, from: Date())
        // 注意: 市场现在按 ticker 推断 (Q26=C)，fixture 的代码必须像真实标的 ——
        // 6 位数字 → 中国内地 → 税率 0%。用 "A" 这种纯字母会被判成美股 10%。
        try db.upsertAssets([Asset(key: "001234", name: "a", ticker: "001234",
                                   pool: .domestic, currency: "CNY")])
        try db.upsertHoldings([Holding(assetKey: "001234", quantity: 10, costBasis: 100,
                                       currency: "CNY", asOfDate: "2026-09-29")])
        try db.replaceDividends(assetKey: "001234", records: [
            rec("001234", "\(y - 2)-01-05", 1.0, "CNY"),
            rec("001234", "\(y - 1)-01-05", 1.0, "CNY"),
            rec("001234", "\(y)-01-05", 0.7, "CNY"),   // 今年已发生
        ], meta: meta("001234", .ok, "eastmoney"))
        let row = try XCTUnwrap(try Repository(db: db).fetchAssetPerspectives().first?.dividend)
        XCTAssertEqual(row.yearCurrent, y)
        XCTAssertEqual(row.actualYtdCny ?? 0, 7.0, accuracy: 1e-9)   // 0.7 × 10 × 1.0 × (1−0)
    }

    /// 抓取失败时保留上一次的有效记录, 只把状态改成 failed (NULL 态展示, 数据不丢).
    func testFailedFetchKeepsRecordsButMarksNullState() throws {
        let db = try makeDB("fail.db")
        let y = Calendar.current.component(.year, from: Date())
        try db.upsertAssets([Asset(key: "A", name: "a", pool: .domestic, currency: "CNY")])
        try db.upsertHoldings([Holding(assetKey: "A", quantity: 1, costBasis: 1,
                                       currency: "CNY", asOfDate: "2026-09-29")])
        try db.replaceDividends(assetKey: "A",
                                records: [rec("A", "\(y - 1)-01-05", 1.0, "CNY")],
                                meta: meta("A", .ok, "eastmoney"))
        XCTAssertEqual(try db.fetchDividends().count, 1)

        try db.replaceDividends(assetKey: "A", records: [],
                                meta: meta("A", .failed, "eastmoney"))
        XCTAssertEqual(try db.fetchDividends().count, 1, "失败时不能清掉已抓到的记录")
        XCTAssertEqual(try db.fetchDividendFetchMetas()["A"]?.status, .failed)
        let row = try XCTUnwrap(try Repository(db: db).fetchAssetPerspectives().first?.dividend)
        XCTAssertNil(row.estimatedTotal, "failed = NULL 态 → 显示「—」")
        XCTAssertEqual(try Repository(db: db).fetchDividendSummary().coveredCount, 0)
    }

    /// 成功抓取会整体替换该标的的记录 (旧年份不残留).
    func testSuccessfulFetchReplacesRecords() throws {
        let db = try makeDB("replace.db")
        try db.upsertAssets([Asset(key: "A", name: "a", pool: .domestic, currency: "CNY")])
        try db.replaceDividends(assetKey: "A",
                                records: [rec("A", "2020-01-01", 9.0, "CNY")],
                                meta: meta("A", .ok, "eastmoney"))
        try db.replaceDividends(assetKey: "A",
                                records: [rec("A", "2025-01-01", 1.0, "CNY")],
                                meta: meta("A", .ok, "eastmoney"))
        XCTAssertEqual(try db.fetchDividends().map(\.amount), [1.0])
    }

    // MARK: - 迁移与备份

    /// v7 旧库 → v8: 迁移必须重建两张表并推进版本号.
    func testMigrationFromV7CreatesDividendTables() throws {
        let db = try makeDB("migrate.db")
        XCTAssertEqual(db.currentSchemaVersion(), Schema.version)
        // 模拟一个 v7 库: 丢掉 v8 的表, 版本回退.
        try db.exec("DROP TABLE dividends")
        try db.exec("DROP TABLE dividend_fetch_meta")
        try db.exec("UPDATE schema_meta SET value = '7' WHERE key = 'version'")
        XCTAssertEqual(db.currentSchemaVersion(), 7)
        try db.migrate()
        XCTAssertEqual(db.currentSchemaVersion(), 8)
        try db.upsertAssets([Asset(key: "A", name: "a", pool: .domestic, currency: "CNY")])
        try db.replaceDividends(assetKey: "A",
                                records: [rec("A", "2025-01-01", 1.0, "CNY")],
                                meta: meta("A", .ok, "eastmoney"))
        XCTAssertEqual(try db.fetchDividends().count, 1)
        XCTAssertEqual(try db.fetchDividendFetchMetas()["A"]?.status, .ok)
    }

    /// exportJSON → importJSON 必须保住逐笔股息与抓取元数据.
    func testExportImportRoundtripDividends() throws {
        let db = try makeDB("export.db")
        try db.upsertAssets([Asset(key: "A", name: "a", pool: .domestic, currency: "CNY")])
        try db.replaceDividends(assetKey: "A", records: [
            rec("A", "2024-06-20", 0.3, "CNY"),
            rec("A", "2025-06-20", 0.4, "CNY"),
        ], meta: DividendFetchMeta(assetKey: "A", status: .ok,
                                   source: "eastmoney", fetchedAt: "2026-09-29 13:00:00"))
        let jsonURL = tmpDir.appendingPathComponent("div-backup.json")
        try BackupManager(db: db, backupDir: tmpDir).exportJSON(to: jsonURL)

        let restored = try makeDB("restore.db")
        try BackupManager(db: restored, backupDir: tmpDir).importJSON(from: jsonURL)
        XCTAssertEqual(try restored.fetchDividends().count, 2)
        let m = try restored.fetchDividendFetchMetas()["A"]
        XCTAssertEqual(m?.status, .ok)
        XCTAssertEqual(m?.source, "eastmoney")
        XCTAssertEqual(m?.fetchedAt, "2026-09-29 13:00:00")
    }

    /// 旧备份 (没有 dividends / dividend_fetch_meta 键) 仍可导入, 且不会伪造股息.
    func testLegacyBackupWithoutDividendsStillImports() throws {
        let fixture = tmpDir.appendingPathComponent("legacy.json")
        try """
        {
          "assets" : [
            {"asset_class" : "us_equity", "currency" : "USD", "key" : "GOOG", "name" : "谷歌", "pool" : "overseas", "ticker" : "GOOG"}
          ],
          "holdings" : [],
          "schema_version" : 7
        }
        """.write(to: fixture, atomically: true, encoding: .utf8)
        let db = try makeDB("legacy.db")
        try BackupManager(db: db, backupDir: tmpDir).importJSON(from: fixture)
        XCTAssertEqual(try db.fetchAssets().count, 1)
        XCTAssertTrue(try db.fetchDividends().isEmpty)
        XCTAssertTrue(try db.fetchDividendFetchMetas().isEmpty)
    }

    /// clearAssetsData (导入备份时勾「清空」) 必须连股息一起清掉.
    func testClearAssetsDataAlsoClearsDividends() throws {
        let db = try makeDB("clear.db")
        try db.upsertAssets([Asset(key: "A", name: "a", pool: .domestic, currency: "CNY")])
        try db.replaceDividends(assetKey: "A",
                                records: [rec("A", "2025-01-01", 1.0, "CNY")],
                                meta: meta("A", .ok, "eastmoney"))
        try db.clearAssetsData()
        XCTAssertTrue(try db.fetchDividends().isEmpty)
        XCTAssertTrue(try db.fetchDividendFetchMetas().isEmpty)
    }

    /// 删除标的时股息记录与元数据一并删除.
    func testDeleteAssetRemovesDividends() throws {
        let db = try makeDB("delete.db")
        try db.upsertAssets([Asset(key: "A", name: "a", pool: .domestic, currency: "CNY")])
        try db.replaceDividends(assetKey: "A",
                                records: [rec("A", "2025-01-01", 1.0, "CNY")],
                                meta: meta("A", .ok, "eastmoney"))
        try db.deleteAsset(key: "A")
        XCTAssertTrue(try db.fetchDividends().isEmpty)
        XCTAssertTrue(try db.fetchDividendFetchMetas().isEmpty)
    }

    // MARK: - helpers

    private func rec(_ key: String, _ exDate: String, _ amount: Double,
                     _ currency: String) -> DividendRecord {
        DividendRecord(assetKey: key, exDate: exDate, amount: amount,
                       currency: currency, source: "test")
    }

    private func meta(_ key: String, _ status: DividendFetchStatus,
                      _ source: String) -> DividendFetchMeta {
        DividendFetchMeta(assetKey: key, status: status, source: source,
                          fetchedAt: "2026-09-29 13:00:00")
    }
}
