import XCTest
@testable import PortfolioCore

/// 财务分析重做的保真测试: 用 投资组合情况(模拟数据).xlsx 的 11 列模拟数据作输入,
/// 断言派生行与 xlsx 公式的缓存计算结果一致 (数值来自导出时的缓存值).
final class QuarterlyMetricsTests: XCTestCase {

    // MARK: - xlsx 模拟数据 (B..L 共 11 列)

    private func mockReports() -> [QuarterlyReport] {
        [
            QuarterlyReport(periodEnd: "2023-12-31",
                marketValue: 22000, totalCost: 22000),
            QuarterlyReport(periodEnd: "2024-03-31",
                marketValue: 27402.2172420332, totalCost: 26863.0612505045,
                interestDomestic: 22.72, interestOverseas: 10.53,
                dividendDomestic: 2.095, dividendOverseas: 121.945,
                capitalGainDomestic: 0, capitalGainOverseas: 733.9675, taxes: 0.06),
            QuarterlyReport(periodEnd: "2024-06-30",
                marketValue: 33623.7622307932, totalCost: 32394.8485860896,
                interestDomestic: 22.72, interestOverseas: 10.53,
                dividendDomestic: 2.095, dividendOverseas: 121.945,
                capitalGainDomestic: 0, capitalGainOverseas: 733.9675, taxes: 0.06),
            QuarterlyReport(periodEnd: "2024-09-30",
                marketValue: 40724.2426586369, totalCost: 38639.6308098423,
                interestDomestic: 22.72, interestOverseas: 10.53,
                dividendDomestic: 2.095, dividendOverseas: 121.945,
                capitalGainDomestic: 0, capitalGainOverseas: 733.9675, taxes: 0.06),
            QuarterlyReport(periodEnd: "2024-12-31",
                marketValue: 48760.23, totalCost: 45639.6,
                interestDomestic: 22.72, interestOverseas: 10.53,
                dividendDomestic: 2.095, dividendOverseas: 121.945,
                capitalGainDomestic: 0, capitalGainOverseas: 733.9675, taxes: 0.06),
            QuarterlyReport(periodEnd: "2025-03-31",
                marketValue: 71523.0448233832, totalCost: 67847.1197220689,
                interestDomestic: 11.45, interestOverseas: 5.97,
                dividendDomestic: 171.8, dividendOverseas: 378.8,
                capitalGainDomestic: 0, capitalGainOverseas: 2710.6, taxes: 34.78),
            QuarterlyReport(periodEnd: "2025-06-30",
                marketValue: 100460.964850945, totalCost: 96301.8255283092,
                interestDomestic: 11.45, interestOverseas: 5.97,
                dividendDomestic: 171.8, dividendOverseas: 378.8,
                capitalGainDomestic: 0, capitalGainOverseas: 2710.6, taxes: 34.78),
            QuarterlyReport(periodEnd: "2025-09-30",
                marketValue: 136355.150350588, totalCost: 131821.385831236,
                interestDomestic: 11.45, interestOverseas: 5.97,
                dividendDomestic: 171.8, dividendOverseas: 378.8,
                capitalGainDomestic: 0, capitalGainOverseas: 2710.6, taxes: 34.78),
            QuarterlyReport(periodEnd: "2025-12-31",
                marketValue: 179924.06, totalCost: 175156.1,
                interestDomestic: 11.45, interestOverseas: 5.97,
                dividendDomestic: 171.8, dividendOverseas: 378.8,
                capitalGainDomestic: 0, capitalGainOverseas: 2710.6, taxes: 34.78),
            QuarterlyReport(periodEnd: "2026-03-31",
                marketValue: 227387.24, totalCost: 221361.51,
                interestDomestic: 346.51, interestOverseas: 6.66,
                dividendDomestic: 26.7, dividendOverseas: 355.57,
                capitalGainDomestic: 2160.25, capitalGainOverseas: 7994.88, taxes: 8.76),
            QuarterlyReport(periodEnd: "2026-06-30",
                marketValue: 332334.24, totalCost: 328979.7,
                interestDomestic: 53.81, interestOverseas: 249.02,
                dividendDomestic: 253.8, dividendOverseas: 1562.67,
                capitalGainDomestic: 2162.76, capitalGainOverseas: 22952.55, taxes: 1995.84),
        ]
    }

    private func computed() -> [QuarterComputed] { QuarterlyMetrics.compute(mockReports()) }

    private func assertEqual(_ a: Double?, _ b: Double, accuracy: Double = 1e-9,
                             _ msg: String = "", file: StaticString = #filePath, line: UInt = #line) {
        guard let a else { XCTFail("nil 但期望有值 \(msg)", file: file, line: line); return }
        XCTAssertEqual(a, b, accuracy: accuracy, msg, file: file, line: line)
    }

    // MARK: - 资产块

    func testAssetBlockMatchesXlsx() {
        let c = computed()
        // 基准列 B: 累计已实现回报 0, 累计净总回报 0, 指数 1 (xlsx B6/B11/B13).
        XCTAssertEqual(c[0].cumRealizedReturn, 0, accuracy: 1e-12)
        XCTAssertEqual(c[0].cumNetReturn, 0, accuracy: 1e-12)
        assertEqual(c[0].cumNetReturnRate, 1)
        XCTAssertNil(c[0].periodDays)
        XCTAssertNil(c[0].quarterReturnRate)   // 基准列无回报可算

        // C 列 (2024Q1).
        assertEqual(c[1].principal, 25971.8037505045)
        assertEqual(c[1].cumRealizedReturn, 891.2575)
        assertEqual(c[1].cumInterest, 33.25)
        assertEqual(c[1].cumDividend, 124.04)
        assertEqual(c[1].cumRealizedGain, 733.9675)
        assertEqual(c[1].unrealizedGain, 539.1559915287)
        assertEqual(c[1].cumNetReturn, 1430.4134915287)
        assertEqual(c[1].cumNetReturnRate, 1.06501879506949)
        XCTAssertEqual(c[1].periodDays, 90)

        // D 列.
        assertEqual(c[2].principal, 30612.3335860896)
        assertEqual(c[2].cumRealizedReturn, 1782.515)
        assertEqual(c[2].unrealizedGain, 1228.9136447036)
        assertEqual(c[2].cumNetReturn, 3011.4286447036)
        assertEqual(c[2].cumNetReturnRate, 1.12646677347753)

        // L 列 (最后一列).
        assertEqual(c[10].principal, 274175.01)
        assertEqual(c[10].cumRealizedReturn, 54804.69)
        assertEqual(c[10].cumInterest, 858.68)
        assertEqual(c[10].cumDividend, 4897.3)
        assertEqual(c[10].cumRealizedGain, 49048.71)
        assertEqual(c[10].unrealizedGain, 3354.53999999998)
        assertEqual(c[10].cumNetReturn, 58159.23)
        assertEqual(c[10].cumNetReturnRate, 1.7718026946631)
    }

    // MARK: - 现金流块

    func testCashFlowBlockMatchesXlsx() {
        let c = computed()
        // 当季三项合计.
        assertEqual(c[1].interest, 33.25)
        assertEqual(c[1].dividend, 124.04)
        assertEqual(c[1].capitalGain, 733.9675)
        assertEqual(c[10].interest, 302.83)
        assertEqual(c[10].dividend, 1816.47)
        assertEqual(c[10].capitalGain, 25115.31)

        // 新投资 = 总成本 − 上季总成本 − 税.
        assertEqual(c[1].newInvestment, 4863.0012505045)
        assertEqual(c[2].newInvestment, 5531.7273355851)
        assertEqual(c[10].newInvestment, 105622.35)
        // 一次投资 = 本金差.
        assertEqual(c[1].primaryInvestment, 3971.8037505045)
        assertEqual(c[2].primaryInvestment, 4640.5298355851)
        assertEqual(c[10].primaryInvestment, 80383.58)
        // 二次投资 = 新投资 − 一次投资.
        assertEqual(c[1].secondaryInvestment, 891.197499999999)
        assertEqual(c[2].secondaryInvestment, 891.197499999999)
        assertEqual(c[10].secondaryInvestment, 25238.77)
        assertEqual(c[1].secondaryShare, 0.18326080008874)
        assertEqual(c[2].secondaryShare, 0.161106548811075)
        assertEqual(c[10].secondaryShare, 0.238952929943331)

        // 净总回报 (当季).
        assertEqual(c[1].quarterNetReturn, 1430.4134915287)
        assertEqual(c[2].quarterNetReturn, 1581.0151531749)
        assertEqual(c[10].quarterNetReturn, 24563.42)

        // (股息+利息)/净总回报.
        assertEqual(c[1].incomeShare, 0.109961211168319)
        assertEqual(c[10].incomeShare, 0.0862787022328324)
    }

    // MARK: - 收益率 / 年化 / 均值 / 标准差 / CI

    func testReturnStatsMatchXlsx() {
        let c = computed()
        // 季度收益率 = 当季净总回报 / 上季总市值.
        assertEqual(c[1].quarterReturnRate, 0.0650187950694864)
        assertEqual(c[2].quarterReturnRate, 0.0576966140809121)
        assertEqual(c[10].quarterReturnRate, 0.108024619147495)
        // 年化 = ×4.
        assertEqual(c[1].annualizedRate, 0.260075180277945)
        assertEqual(c[2].annualizedRate, 0.230786456323648)
        assertEqual(c[10].annualizedRate, 0.432098476589979)
        // 均值 = 累计净总回报率^(4/k) − 1.
        assertEqual(c[1].meanRate, 0.286557167017523)
        assertEqual(c[2].meanRate, 0.268927391748854)
        assertEqual(c[4].meanRate, 0.24107330582978)
        assertEqual(c[10].meanRate, 0.257089338585093)
        // 标准差 = STDEVP(年化序列自第一个非基准列).
        assertEqual(c[1].stdDev, 0)
        assertEqual(c[2].stdDev, 0.0146443619771486)
        assertEqual(c[4].stdDev, 0.0264441210914633)
        assertEqual(c[10].stdDev, 0.0871148540148053)
        // ±95% CI = 均值 ± 1.96σ.
        assertEqual(c[2].ciPlus, 0.297630341224065)
        assertEqual(c[2].ciMinus, 0.240224442273643)
        assertEqual(c[10].ciPlus, 0.427834452454111)
        assertEqual(c[10].ciMinus, 0.0863442247160746)
    }

    // MARK: - YoY% (xlsx 预留空行, 此处为公式派生)

    func testYoYRows() {
        let c = computed()
        // 基准列无现金流 → k=4 时分母为 nil → YoY 空.
        XCTAssertNil(c[4].interestYoY)
        // H (k=5) 对比 C (k=1).
        assertEqual(c[5].interestYoY, -0.4760902255639098)
        assertEqual(c[5].dividendYoY, 3.4388906804256694)
        assertEqual(c[5].capitalGainYoY, 2.6930790532278337)
        // L (k=10) 对比 H (k=6).
        assertEqual(c[10].interestYoY, 16.38404133180253)
        assertEqual(c[10].dividendYoY, 2.2990737377406463)
        assertEqual(c[10].capitalGainYoY, 8.265590644137829)
        assertEqual(c[10].quarterNetReturnYoY, 5.529639148045975)
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
        assertEqual(c[10].cumNetReturn, 58159.23)
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
            marketValue: 332334.24, totalCost: 328979.7,
            interestDomestic: 53.81, interestOverseas: 249.02,
            dividendDomestic: 253.8, dividendOverseas: 1562.67,
            capitalGainDomestic: 2162.76, capitalGainOverseas: 22952.55,
            taxes: 1995.84, source: "manual")
        try db.upsertQuarterlyReports([q])

        var fetched = try db.fetchQuarterlyReports()
        XCTAssertEqual(fetched.count, 1)
        XCTAssertEqual(fetched[0].periodEnd, "2026-06-30")
        XCTAssertEqual(fetched[0].marketValue ?? 0, 332334.24, accuracy: 1e-9)
        XCTAssertEqual(fetched[0].taxes ?? 0, 1995.84, accuracy: 1e-9)
        XCTAssertEqual(fetched[0].source, "manual")

        // 二次 upsert 同一季末 = 更新 (录入后仍可编辑).
        q.totalCost = 330000
        q.taxes = nil
        try db.upsertQuarterlyReports([q])
        fetched = try db.fetchQuarterlyReports()
        XCTAssertEqual(fetched.count, 1)
        XCTAssertEqual(fetched[0].totalCost ?? 0, 330000, accuracy: 1e-9)
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
