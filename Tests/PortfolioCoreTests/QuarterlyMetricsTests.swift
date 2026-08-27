import XCTest
@testable import PortfolioCore

/// 财务分析重做的保真测试: 用一组**完全虚构**的 11 列季度数据作输入,
/// 断言派生行与既定公式链一致。
///
/// 注意: fixture 是编造的示例数据, 不含任何真实持仓/金额; 期望值由
/// `QuarterlyMetrics.compute` 自身生成后固化, 用来锁住公式行为不回归。
final class QuarterlyMetricsTests: XCTestCase {

    // MARK: - 虚构季度数据 (B..L 共 11 列)

    private func mockReports() -> [QuarterlyReport] {
        [
            QuarterlyReport(periodEnd: "2023-12-31",
                marketValue: 30000, totalCost: 30000),
            QuarterlyReport(periodEnd: "2024-03-31",
                marketValue: 31500.1234, totalCost: 30800.5,
                interestDomestic: 12.5, interestOverseas: 3.25,
                dividendDomestic: 1.5, dividendOverseas: 40.75,
                capitalGainDomestic: 0, capitalGainOverseas: 120.5, taxes: 0.05),
            QuarterlyReport(periodEnd: "2024-06-30",
                marketValue: 33250.75, totalCost: 31550.25,
                interestDomestic: 12.5, interestOverseas: 3.25,
                dividendDomestic: 1.5, dividendOverseas: 40.75,
                capitalGainDomestic: 0, capitalGainOverseas: 120.5, taxes: 0.05),
            QuarterlyReport(periodEnd: "2024-09-30",
                marketValue: 35010.5, totalCost: 32300.75,
                interestDomestic: 12.5, interestOverseas: 3.25,
                dividendDomestic: 1.5, dividendOverseas: 40.75,
                capitalGainDomestic: 0, capitalGainOverseas: 120.5, taxes: 0.05),
            QuarterlyReport(periodEnd: "2024-12-31",
                marketValue: 36800.25, totalCost: 33050.5,
                interestDomestic: 12.5, interestOverseas: 3.25,
                dividendDomestic: 1.5, dividendOverseas: 40.75,
                capitalGainDomestic: 0, capitalGainOverseas: 120.5, taxes: 0.05),
            QuarterlyReport(periodEnd: "2025-03-31",
                marketValue: 39500.5, totalCost: 35500.25,
                interestDomestic: 8.5, interestOverseas: 4.25,
                dividendDomestic: 95.5, dividendOverseas: 210.25,
                capitalGainDomestic: 0, capitalGainOverseas: 1500.75, taxes: 21.5),
            QuarterlyReport(periodEnd: "2025-06-30",
                marketValue: 42800.75, totalCost: 38200.5,
                interestDomestic: 8.5, interestOverseas: 4.25,
                dividendDomestic: 95.5, dividendOverseas: 210.25,
                capitalGainDomestic: 0, capitalGainOverseas: 1500.75, taxes: 21.5),
            QuarterlyReport(periodEnd: "2025-09-30",
                marketValue: 46200.25, totalCost: 41050.75,
                interestDomestic: 8.5, interestOverseas: 4.25,
                dividendDomestic: 95.5, dividendOverseas: 210.25,
                capitalGainDomestic: 0, capitalGainOverseas: 1500.75, taxes: 21.5),
            QuarterlyReport(periodEnd: "2025-12-31",
                marketValue: 49800.5, totalCost: 44100.25,
                interestDomestic: 8.5, interestOverseas: 4.25,
                dividendDomestic: 95.5, dividendOverseas: 210.25,
                capitalGainDomestic: 0, capitalGainOverseas: 1500.75, taxes: 21.5),
            QuarterlyReport(periodEnd: "2026-03-31",
                marketValue: 53500.75, totalCost: 47300.5,
                interestDomestic: 180.25, interestOverseas: 5.5,
                dividendDomestic: 20.5, dividendOverseas: 180.75,
                capitalGainDomestic: 900.5, capitalGainOverseas: 4200.25, taxes: 5.25),
            QuarterlyReport(periodEnd: "2026-06-30",
                marketValue: 57800.25, totalCost: 51050.75,
                interestDomestic: 30.5, interestOverseas: 120.25,
                dividendDomestic: 150.25, dividendOverseas: 800.5,
                capitalGainDomestic: 950.75, capitalGainOverseas: 9000.25, taxes: 800.5),
        ]
    }

    private func computed() -> [QuarterComputed] { QuarterlyMetrics.compute(mockReports()) }

    private func assertEqual(_ a: Double?, _ b: Double, accuracy: Double = 1e-9,
                             _ msg: String = "", file: StaticString = #filePath, line: UInt = #line) {
        guard let a else { XCTFail("nil 但期望有值 \(msg)", file: file, line: line); return }
        XCTAssertEqual(a, b, accuracy: accuracy, msg, file: file, line: line)
    }

    // MARK: - 资产块

    func testAssetBlockMatchesFixture() {
        let c = computed()
        // 基准列: 累计已实现回报 0, 累计净总回报 0, 指数 1.
        XCTAssertEqual(c[0].cumRealizedReturn, 0, accuracy: 1e-12)
        XCTAssertEqual(c[0].cumNetReturn, 0, accuracy: 1e-12)
        assertEqual(c[0].cumNetReturnRate, 1)
        XCTAssertNil(c[0].periodDays)
        XCTAssertNil(c[0].quarterReturnRate)

        // C 列 (2024Q1).
        assertEqual(c[1].principal, 30622.0)
        assertEqual(c[1].cumRealizedReturn, 178.5)
        assertEqual(c[1].cumInterest, 15.75)
        assertEqual(c[1].cumDividend, 42.25)
        assertEqual(c[1].cumRealizedGain, 120.5)
        assertEqual(c[1].unrealizedGain, 699.6234000000004)
        assertEqual(c[1].cumNetReturn, 878.1234000000004)
        assertEqual(c[1].cumNetReturnRate, 1.02927078)
        XCTAssertEqual(c[1].periodDays, 90)

        // D 列.
        assertEqual(c[2].principal, 31193.25)
        assertEqual(c[2].cumRealizedReturn, 357.0)
        assertEqual(c[2].unrealizedGain, 1700.5)
        assertEqual(c[2].cumNetReturn, 2057.5)
        assertEqual(c[2].cumNetReturnRate, 1.0678070694481787)

        // L 列 (最后一列).
        assertEqual(c[10].principal, 26519.5)
        assertEqual(c[10].cumRealizedReturn, 24531.25)
        assertEqual(c[10].cumInterest, 450.5)
        assertEqual(c[10].cumDividend, 2544.0)
        assertEqual(c[10].cumRealizedGain, 21536.75)
        assertEqual(c[10].unrealizedGain, 6749.5)
        assertEqual(c[10].cumNetReturn, 31280.75)
        assertEqual(c[10].cumNetReturnRate, 1.940161195482305)
    }

    // MARK: - 现金流块

    func testCashFlowBlockMatchesFixture() {
        let c = computed()
        // 当季三项合计.
        assertEqual(c[1].interest, 15.75)
        assertEqual(c[1].dividend, 42.25)
        assertEqual(c[1].capitalGain, 120.5)
        assertEqual(c[10].interest, 150.75)
        assertEqual(c[10].dividend, 950.75)
        assertEqual(c[10].capitalGain, 9951.0)

        // 新投资 = 总成本 − 上季总成本 − 税.
        assertEqual(c[1].newInvestment, 800.45)
        assertEqual(c[2].newInvestment, 749.7)
        assertEqual(c[10].newInvestment, 2949.75)
        // 一次投资 = 本金差.
        assertEqual(c[1].primaryInvestment, 622.0)
        assertEqual(c[2].primaryInvestment, 571.25)
        assertEqual(c[10].primaryInvestment, -7302.25)
        // 二次投资 = 新投资 − 一次投资.
        assertEqual(c[1].secondaryInvestment, 178.45000000000005)
        assertEqual(c[2].secondaryInvestment, 178.45000000000005)
        assertEqual(c[10].secondaryInvestment, 10252.0)
        assertEqual(c[1].secondaryShare, 0.22293709788244118)
        assertEqual(c[2].secondaryShare, 0.23802854475123386)
        assertEqual(c[10].secondaryShare, 3.4755487753199423)

        // 净总回报 (当季).
        assertEqual(c[1].quarterNetReturn, 878.1234000000004)
        assertEqual(c[2].quarterNetReturn, 1179.3765999999996)
        assertEqual(c[10].quarterNetReturn, 11601.75)

        // (股息+利息)/净总回报.
        assertEqual(c[1].incomeShare, 0.06604994241128294)
        assertEqual(c[10].incomeShare, 0.09494257331868038)
    }

    // MARK: - 收益率 / 年化 / 均值 / 标准差 / CI

    func testReturnStatsMatchFixture() {
        let c = computed()
        // 季度收益率 = 当季净总回报 / 上季总市值.
        assertEqual(c[1].quarterReturnRate, 0.029270780000000014)
        assertEqual(c[2].quarterReturnRate, 0.03744038031292282)
        assertEqual(c[10].quarterReturnRate, 0.21685210020420276)
        // 年化 = ×4.
        assertEqual(c[1].annualizedRate, 0.11708312000000005)
        assertEqual(c[2].annualizedRate, 0.1497615212516913)
        assertEqual(c[10].annualizedRate, 0.867408400816811)
        // 均值 = 累计净总回报率^(4/k) − 1.
        assertEqual(c[1].meanRate, 0.12232483974752029)
        assertEqual(c[2].meanRate, 0.14021193756350758)
        assertEqual(c[4].meanRate, 0.1444415234756955)
        assertEqual(c[10].meanRate, 0.303572306680959)
        // 标准差 = STDEVP(年化序列自第一个非基准列).
        assertEqual(c[1].stdDev, 0.0)
        assertEqual(c[2].stdDev, 0.016339200625845617)
        assertEqual(c[4].stdDev, 0.012235655292809426)
        assertEqual(c[10].stdDev, 0.2192797834704766)
        // ±95% CI = 均值 ± 1.96σ.
        assertEqual(c[2].ciPlus, 0.172236770790165)
        assertEqual(c[2].ciMinus, 0.10818710433685017)
        assertEqual(c[10].ciPlus, 0.7333606822830931)
        assertEqual(c[10].ciMinus, -0.1262160689211751)
    }

    // MARK: - YoY%

    func testYoYRows() {
        let c = computed()
        // 基准列无现金流 → k=4 时分母为 nil → YoY 空.
        XCTAssertNil(c[4].interestYoY)
        // H (k=5) 对比 C (k=1).
        assertEqual(c[5].interestYoY, -0.19047619047619047)
        assertEqual(c[5].dividendYoY, 6.236686390532545)
        assertEqual(c[5].capitalGainYoY, 11.45435684647303)
        // L (k=10) 对比 H (k=6).
        assertEqual(c[10].interestYoY, 10.823529411764707)
        assertEqual(c[10].dividendYoY, 2.1095666394112835)
        assertEqual(c[10].capitalGainYoY, 5.630684657671164)
        assertEqual(c[10].quarterNetReturnYoY, 3.7955978092384006)
    }

    // MARK: - 空值 / 半填 / 单列语义

    func testEmptyInput() {
        XCTAssertTrue(QuarterlyMetrics.compute([]).isEmpty)
    }

    func testSingleBaselineColumn() {
        let c = QuarterlyMetrics.compute([
            QuarterlyReport(periodEnd: "2026-06-30", marketValue: 10000, totalCost: 10000)])
        XCTAssertEqual(c.count, 1)
        XCTAssertEqual(c[0].cumRealizedReturn, 0)
        XCTAssertEqual(c[0].cumNetReturn, 0)
        assertEqual(c[0].cumNetReturnRate, 1)
        assertEqual(c[0].principal, 10000)
        assertEqual(c[0].unrealizedGain, 0)
        XCTAssertNil(c[0].periodDays)
        XCTAssertNil(c[0].quarterReturnRate)
        XCTAssertNil(c[0].meanRate)
        XCTAssertNil(c[0].stdDev)
    }

    func testPartialFillTreatsMissingAsZero() {
        // 只填利息境内: 合计 = 境内 (Excel SUM 语义), 其余行可算.
        let c = QuarterlyMetrics.compute([
            QuarterlyReport(periodEnd: "2026-03-31", marketValue: 10000, totalCost: 10000),
            QuarterlyReport(periodEnd: "2026-06-30", marketValue: 10500, totalCost: 10200,
                            interestDomestic: 100)])
        assertEqual(c[1].interest, 100)
        assertEqual(c[1].cumInterest, 100)
        XCTAssertEqual(c[1].cumDividend, 0)
        assertEqual(c[1].cumRealizedReturn, 100)
        assertEqual(c[1].principal, 10100)
        assertEqual(c[1].unrealizedGain, 300)
        assertEqual(c[1].cumNetReturn, 400)
        assertEqual(c[1].quarterNetReturn, 400)
        assertEqual(c[1].quarterReturnRate, 0.04)   // 400 / 上季总市值 10000
        assertEqual(c[1].newInvestment, 200)         // 10200 − 10000 − 0
        assertEqual(c[1].primaryInvestment, 100)     // 10100 − 10000
        assertEqual(c[1].secondaryInvestment, 100)
        XCTAssertEqual(c[1].periodDays, 90)
    }

    func testMissingMarketValueBlanksDerivedButKeepsChain() {
        // 中间列缺总市值: 该列未实现/收益率空, 但累计链与后续列照常.
        let c = QuarterlyMetrics.compute([
            QuarterlyReport(periodEnd: "2026-03-31", marketValue: 10000, totalCost: 10000),
            QuarterlyReport(periodEnd: "2026-06-30", totalCost: 10200, interestDomestic: 100),
            QuarterlyReport(periodEnd: "2026-09-30", marketValue: 11000, totalCost: 10400)])
        XCTAssertNil(c[1].unrealizedGain)
        assertEqual(c[1].quarterReturnRate, 0.01)     // 该列自身收益率仍可算 (分母是上季总市值)
        assertEqual(c[1].cumInterest, 100)
        assertEqual(c[1].cumNetReturn, 100)          // 未实现按 0 计
        XCTAssertNil(c[2].quarterReturnRate)          // 上季总市值缺失 → 分母空
        assertEqual(c[2].cumNetReturnRate, 1.01)      // 1 × (1 + 0) 复利链不断
        XCTAssertEqual(c[2].periodDays, 90)           // DAYS360 欧洲法 (同 xlsx)
    }

    func testUnsortedInputIsSortedByPeriodEnd() {
        let c = QuarterlyMetrics.compute(mockReports().reversed())
        XCTAssertEqual(c.map(\.report.periodEnd), mockReports().map(\.periodEnd))
        assertEqual(c[10].cumNetReturn, 31280.75)
    }

    // MARK: - 日期工具

    func testQuarterHelpers() {
        XCTAssertEqual(QuarterlyMetrics.quarterLabel("2026-06-30"), "2026 Q2")
        XCTAssertEqual(QuarterlyMetrics.quarterLabel("2026-12-31"), "2026 Q4")
        XCTAssertEqual(QuarterlyMetrics.nextQuarterEnd(after: "2026-06-30"), "2026-09-30")
        XCTAssertEqual(QuarterlyMetrics.nextQuarterEnd(after: "2025-12-31"), "2026-03-31")
        XCTAssertEqual(QuarterlyMetrics.nextQuarterEnd(after: "2024-02-29"), "2024-05-31")
    }
}

/// quarterly_reports 表的存取往返 (内存库).
final class QuarterlyReportDatabaseTests: XCTestCase {

    private func makeDB() throws -> Database {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("pm-tests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return try Database(path: dir.appendingPathComponent("t.db").path)
    }

    func testUpsertFetchDeleteRoundtrip() throws {
        let db = try makeDB()
        defer { try? FileManager.default.removeItem(at: URL(fileURLWithPath: db.path).deletingLastPathComponent()) }

        var q = QuarterlyReport(periodEnd: "2026-06-30",
            marketValue: 57800.25, totalCost: 51050.75,
            interestDomestic: 30.5, interestOverseas: 120.25,
            dividendDomestic: 150.25, dividendOverseas: 800.5,
            capitalGainDomestic: 950.75, capitalGainOverseas: 9000.25,
            taxes: 800.5, source: "manual")
        try db.upsertQuarterlyReports([q])

        var fetched = try db.fetchQuarterlyReports()
        XCTAssertEqual(fetched.count, 1)
        XCTAssertEqual(fetched[0].periodEnd, "2026-06-30")
        XCTAssertEqual(fetched[0].marketValue ?? 0, 57800.25, accuracy: 1e-9)
        XCTAssertEqual(fetched[0].taxes ?? 0, 800.5, accuracy: 1e-9)
        XCTAssertEqual(fetched[0].source, "manual")

        // 二次 upsert 同一季末 = 更新 (录入后仍可编辑).
        q.totalCost = 52000
        q.taxes = nil
        try db.upsertQuarterlyReports([q])
        fetched = try db.fetchQuarterlyReports()
        XCTAssertEqual(fetched.count, 1)
        XCTAssertEqual(fetched[0].totalCost ?? 0, 52000, accuracy: 1e-9)
        XCTAssertNil(fetched[0].taxes)

        // 未填写字段读写为 NULL.
        try db.upsertQuarterlyReports([QuarterlyReport(periodEnd: "2026-09-30")])
        fetched = try db.fetchQuarterlyReports()
        XCTAssertEqual(fetched.count, 2)
        let empty = fetched.first { $0.periodEnd == "2026-09-30" }!
        XCTAssertNil(empty.marketValue)
        XCTAssertNil(empty.totalCost)

        // 删除一列.
        try db.deleteQuarterlyReport(periodEnd: "2026-06-30")
        fetched = try db.fetchQuarterlyReports()
        XCTAssertEqual(fetched.map(\.periodEnd), ["2026-09-30"])
    }

    func testRenameQuarter() throws {
        let db = try makeDB()
        defer { try? FileManager.default.removeItem(at: URL(fileURLWithPath: db.path).deletingLastPathComponent()) }
        try db.upsertQuarterlyReports([
            QuarterlyReport(periodEnd: "2026-06-30", marketValue: 100),
            QuarterlyReport(periodEnd: "2026-09-30", marketValue: 200)])
        try db.renameQuarterlyReport(from: "2026-06-30", to: "2026-03-31")
        XCTAssertEqual(try db.fetchQuarterlyReports().map(\.periodEnd), ["2026-03-31", "2026-09-30"])
        // 改成已存在的季末 → 冲突保护, 不生效.
        try db.renameQuarterlyReport(from: "2026-03-31", to: "2026-09-30")
        XCTAssertEqual(try db.fetchQuarterlyReports().map(\.periodEnd), ["2026-03-31", "2026-09-30"])
    }
}
