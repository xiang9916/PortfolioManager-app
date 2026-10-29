import XCTest
@testable import PortfolioCore

/// 特别关注 · 涨跌归因的口径测试（ADR 0013）。全部使用虚构价格，不含任何真实持仓信息。
///
/// 锁定性质：
/// 1. 精确分解 —— 逐标的贡献之和恒等于组合区间涨跌（浮点 1e-12 内）；
/// 2. 「当日」归因的组合涨跌与合成 K 的 `dayChange` 同口径、逐位一致；
/// 3. 窗口 = 合成 K 的最后 N 根（当日 2 / 近 5 日 6 / 近 20 日 20），期初前向填充；
/// 4. 期初之后才首次有价格的标的不在参与集里，恒等式仍然成立；
/// 5. 无新价（期初之后再没有收盘）→ 涨跌 0%、`stale = true`。
final class FocusAttributionTests: XCTestCase {
    private func bar(_ key: String, _ date: String, _ close: Double) -> PricePoint {
        PricePoint(assetKey: key, date: date, close: close,
                   rawClose: nil, open: close, high: close, low: close)
    }

    private func series(_ key: String, _ weight: Double,
                        _ closes: [(String, Double)]) -> FocusSeries {
        FocusSeries(assetKey: key, name: "示例\(key)", weight: weight,
                    bars: closes.map { bar(key, $0.0, $0.1) })
    }

    private func dates(_ count: Int) -> [String] {
        (1...count).map { String(format: "2026-01-%02d", $0) }
    }

    // MARK: - 精确分解

    /// 两标的 0.7 / 0.3：A +10%、B −10%。
    /// 期初合成 V = (0.7·100/110 + 0.3·200/180) / 1 = 0.96969697
    /// → 组合涨跌 = 1/V − 1 = +3.125%；
    /// ω_A = 0.65625 → 贡献 +6.5625pp；ω_B = 0.34375 → 贡献 −3.4375pp，两者相加正是 +3.125%。
    func testTwoAssetWorkedExampleExactDecomposition() {
        let d = dates(2)
        let board = FocusComposer.compose(series: [
            series("DEMO-A", 0.7, [(d[0], 100), (d[1], 110)]),
            series("DEMO-B", 0.3, [(d[0], 200), (d[1], 180)]),
        ])
        let attribution = try? XCTUnwrap(board.attribution(for: .day))
        XCTAssertEqual(attribution?.totalChange ?? 0, 0.03125, accuracy: 1e-12)
        let a = try? XCTUnwrap(attribution?.contribution(for: "DEMO-A"))
        let b = try? XCTUnwrap(attribution?.contribution(for: "DEMO-B"))
        XCTAssertEqual(a?.change ?? 0, 0.10, accuracy: 1e-12)
        XCTAssertEqual(b?.change ?? 0, -0.10, accuracy: 1e-12)
        XCTAssertEqual(a?.contribution ?? 0, 0.065625, accuracy: 1e-12)
        XCTAssertEqual(b?.contribution ?? 0, -0.034375, accuracy: 1e-12)
        // 展示用权重 = 当前市值权重归一（0.7 / 0.3）。
        XCTAssertEqual(a?.weight ?? 0, 0.7, accuracy: 1e-12)
        XCTAssertEqual(b?.weight ?? 0, 0.3, accuracy: 1e-12)
        XCTAssertEqual((a?.contribution ?? 0) + (b?.contribution ?? 0),
                       attribution?.totalChange ?? 0, accuracy: 1e-12)
    }

    /// 恒等式对每个窗口都成立，且展示权重合计 1。
    func testIdentityHoldsForEveryWindow() {
        let d = dates(20)
        let aCloses = (0..<20).map { (d[$0], 100.0 + Double($0) * 1.3) }
        let bCloses = (0..<20).map { (d[$0], 50.0 - Double($0) * 0.2) }
        let cCloses = (0..<20).map { (d[$0], 80.0 + Double($0 % 3) * 0.5) }
        let board = FocusComposer.compose(series: [
            series("DEMO-A", 0.5, aCloses),
            series("DEMO-B", 0.3, bCloses),
            series("DEMO-C", 0.2, cCloses),
        ])
        XCTAssertEqual(board.attributions.count, FocusWindow.allCases.count)
        for attribution in board.attributions {
            let sum = attribution.contributions.reduce(0.0) { $0 + $1.contribution }
            XCTAssertEqual(sum, attribution.totalChange, accuracy: 1e-12,
                           "窗口 \(attribution.window.rawValue) 的恒等式被破坏")
            let weights = attribution.contributions.reduce(0.0) { $0 + $1.weight }
            XCTAssertEqual(weights, 1.0, accuracy: 1e-12)
        }
    }

    /// 当日归因的组合涨跌与合成 K 的 dayChange 同口径（逐位一致）。
    func testDayAttributionMatchesBoardDayChange() {
        let d = dates(3)
        let board = FocusComposer.compose(series: [
            series("DEMO-A", 0.6, [(d[0], 10), (d[1], 11), (d[2], 12)]),
            series("DEMO-B", 0.4, [(d[0], 20), (d[1], 18), (d[2], 19)]),
        ])
        let attribution = try? XCTUnwrap(board.attribution(for: .day))
        XCTAssertEqual(attribution?.startDate, d[1])
        XCTAssertEqual(attribution?.endDate, d[2])
        XCTAssertEqual(attribution?.totalChange ?? 0, board.dayChange ?? 0, accuracy: 1e-12)
    }

    // MARK: - 窗口

    /// 当日 = 最后 2 根；近 5 日 = 6 根；近 20 日 = 20 根（不足用可得根数）。
    func testWindowBoundaries() {
        let d = dates(20)
        let closes = (0..<20).map { (d[$0], 100.0 + Double($0)) }
        let board = FocusComposer.compose(series: [series("DEMO-A", 1, closes)])
        XCTAssertEqual(board.attribution(for: .day)?.startDate, d[18])
        XCTAssertEqual(board.attribution(for: .day)?.endDate, d[19])
        XCTAssertEqual(board.attribution(for: .week)?.startDate, d[14])
        XCTAssertEqual(board.attribution(for: .week)?.endDate, d[19])
        XCTAssertEqual(board.attribution(for: .month)?.startDate, d[0])
        XCTAssertEqual(board.attribution(for: .month)?.endDate, d[19])
    }

    /// 只有 1 根合成 K → 没有区间，归因为空。
    func testSingleBarHasNoAttribution() {
        let d = dates(1)
        let board = FocusComposer.compose(series: [series("DEMO-A", 1, [(d[0], 100)])])
        XCTAssertTrue(board.attributions.isEmpty)
        XCTAssertNil(board.attribution(for: .day))
    }

    // MARK: - 前向填充 / 中途新增

    /// 无新价：期初之后再没有收盘 → 自身涨跌 0%、stale = true，且不破坏恒等式。
    func testStaleAssetContributesZero() {
        let d = dates(3)
        let board = FocusComposer.compose(series: [
            series("DEMO-A", 0.5, [(d[0], 100), (d[1], 100), (d[2], 110)]),
            // B 的最后收盘停在 d[1]（= 日窗口期初），之后没有新价 → 前向填充、涨跌 0%。
            series("DEMO-B", 0.5, [(d[0], 40), (d[1], 40)]),
        ])
        let attribution = try? XCTUnwrap(board.attribution(for: .day))
        let b = try? XCTUnwrap(attribution?.contribution(for: "DEMO-B"))
        XCTAssertEqual(b?.change ?? 1, 0, accuracy: 1e-12)
        XCTAssertEqual(b?.contribution ?? 1, 0, accuracy: 1e-12)
        XCTAssertEqual(b?.stale, true)
        let a = try? XCTUnwrap(attribution?.contribution(for: "DEMO-A"))
        XCTAssertEqual(a?.stale, false)
        XCTAssertEqual(attribution?.totalChange ?? 0, a?.contribution ?? 0, accuracy: 1e-12)
    }

    /// 期初之后才首次有价格的标的：日窗口参与（期初已有价），20 日窗口不参与。
    func testAssetAppearingMidWindowJoinsOnlyLaterWindows() {
        let d = dates(3)
        let board = FocusComposer.compose(series: [
            series("DEMO-A", 0.5, [(d[0], 100), (d[1], 100), (d[2], 110)]),
            // B 的第一根在 d[1]：20 日窗口从 d[0] 起算时它不在期初参与集。
            series("DEMO-B", 0.5, [(d[1], 40), (d[2], 40)]),
        ])
        let month = try? XCTUnwrap(board.attribution(for: .month))
        XCTAssertNil(month?.contribution(for: "DEMO-B"))
        XCTAssertNotNil(month?.contribution(for: "DEMO-A"))
        XCTAssertEqual(month?.totalChange ?? 0,
                       month?.contribution(for: "DEMO-A")?.contribution ?? 0, accuracy: 1e-12)
        let day = try? XCTUnwrap(board.attribution(for: .day))
        XCTAssertNotNil(day?.contribution(for: "DEMO-B"))
    }

    // MARK: - 汇总口径

    /// 上涨/下跌拆分与只数：只统计贡献严格为正/负的标的，平盘两边都不算。
    func testUpsideDownsideAndCounts() {
        let d = dates(2)
        let board = FocusComposer.compose(series: [
            series("DEMO-A", 0.4, [(d[0], 100), (d[1], 110)]),
            series("DEMO-B", 0.3, [(d[0], 100), (d[1], 80)]),
            series("DEMO-C", 0.3, [(d[0], 100), (d[1], 100)]),
        ])
        let attribution = try? XCTUnwrap(board.attribution(for: .day))
        XCTAssertEqual(attribution?.gainers, 1)
        XCTAssertEqual(attribution?.losers, 1)
        XCTAssertGreaterThan(attribution?.upside ?? -1, 0)
        XCTAssertLessThan(attribution?.downside ?? 1, 0)
        XCTAssertEqual((attribution?.upside ?? 0) + (attribution?.downside ?? 0),
                       attribution?.totalChange ?? 0, accuracy: 1e-12)
    }

    /// 明细按贡献降序；并列时按 assetKey 升序（稳定）。
    func testContributionsSortedByContributionThenKey() {
        let d = dates(2)
        let board = FocusComposer.compose(series: [
            series("DEMO-A", 1, [(d[0], 100), (d[1], 90)]),
            series("DEMO-B", 1, [(d[0], 100), (d[1], 120)]),
            series("DEMO-C", 1, [(d[0], 100), (d[1], 120)]),
        ])
        let keys = board.attribution(for: .day)?.contributions.map(\.assetKey) ?? []
        XCTAssertEqual(keys, ["DEMO-B", "DEMO-C", "DEMO-A"])
    }

    /// 前 3 集中度 = 前 3 大 |贡献| ÷ 全部 |贡献|；单标的恒为 1。
    func testTop3Share() {
        let d = dates(2)
        let single = FocusComposer.compose(series: [
            series("DEMO-A", 1, [(d[0], 100), (d[1], 105)]),
        ])
        XCTAssertEqual(single.attribution(for: .day)?.top3Share ?? 0, 1.0, accuracy: 1e-12)

        let board = FocusComposer.compose(series: [
            series("DEMO-A", 1, [(d[0], 100), (d[1], 110)]),
            series("DEMO-B", 1, [(d[0], 100), (d[1], 104)]),
            series("DEMO-C", 1, [(d[0], 100), (d[1], 102)]),
            series("DEMO-D", 1, [(d[0], 100), (d[1], 98)]),
        ])
        let attribution = try? XCTUnwrap(board.attribution(for: .day))
        let contributions = attribution?.contributions.map { abs($0.contribution) } ?? []
        let gross = contributions.reduce(0, +)
        let expected = contributions.sorted(by: >).prefix(3).reduce(0, +) / gross
        XCTAssertEqual(attribution?.top3Share ?? 0, expected, accuracy: 1e-12)
    }

    /// 全平：贡献全为 0 → 组合涨跌 0，集中度没有意义（nil）。
    func testAllFlatHasNoConcentration() {
        let d = dates(3)
        let board = FocusComposer.compose(series: [
            series("DEMO-A", 0.5, [(d[0], 100), (d[1], 100), (d[2], 100)]),
            series("DEMO-B", 0.5, [(d[0], 50), (d[1], 50), (d[2], 50)]),
        ])
        let attribution = try? XCTUnwrap(board.attribution(for: .day))
        XCTAssertEqual(attribution?.totalChange ?? 1, 0, accuracy: 1e-12)
        XCTAssertNil(attribution?.top3Share)
        XCTAssertEqual(attribution?.gainers, 0)
        XCTAssertEqual(attribution?.losers, 0)
    }

    /// 窗口标签与交易日数（界面用）。
    func testWindowLabelsAndTradingDays() {
        XCTAssertEqual(FocusWindow.day.tradingBars, 2)
        XCTAssertEqual(FocusWindow.week.tradingBars, 6)
        XCTAssertEqual(FocusWindow.month.tradingBars, 20)
        XCTAssertEqual(FocusWindow.day.tradingDays, 1)
        XCTAssertEqual(FocusWindow.day.label, "当日")
        XCTAssertEqual(FocusWindow.week.label, "近 5 日")
        XCTAssertEqual(FocusWindow.month.label, "近 20 日")
        XCTAssertEqual(FocusWindow.allCases.count, 3)
    }
}
