import XCTest
@testable import PortfolioCore

/// 特别关注 · 合成日 K 的口径测试（ADR 0008）。全部使用虚构价格，不含任何真实持仓信息。
///
/// 锁定四条规则：
/// 1. 归一化基准 = 各标的**今收**（1.00）；
/// 2. 合成 = 开/高/低/收 **逐项**按市值权重加权；
/// 3. 无日内价（基金）→ 开 = 昨收、收 = 今收、高/低 = 两者较大/较小；
/// 4. 当日无数据 → 平盘（开 = 高 = 低 = 收），不产生假影线。
final class FocusBoardTests: XCTestCase {
    private func bar(_ key: String, _ date: String, _ close: Double,
                     open: Double? = nil, high: Double? = nil, low: Double? = nil) -> PricePoint {
        PricePoint(assetKey: key, date: date, close: close,
                   open: open, high: high, low: low)
    }

    private let d1 = "2026-10-22"
    private let d2 = "2026-10-23"

    /// 用户给的例 1：股票（开2 高4 低1 收3）+ 基金（昨收2 今收3），等权 1:1
    /// → 合成 开2 高3.5 低1.5 收3；以今收 3 归一后 = 2/3、7/6、1/2、1。
    func testEqualWeightCompositionMatchesWorkedExample() {
        let stock = FocusSeries(assetKey: "DEMO-A", name: "示例股票", weight: 1, bars: [
            bar("DEMO-A", d1, 2, open: 2, high: 2, low: 2),
            bar("DEMO-A", d2, 3, open: 2, high: 4, low: 1),
        ])
        let fund = FocusSeries(assetKey: "DEMO-B", name: "示例基金", weight: 1, bars: [
            bar("DEMO-B", d1, 2),
            bar("DEMO-B", d2, 3),
        ])

        let board = FocusComposer.compose(series: [stock, fund])
        XCTAssertEqual(board.candles.count, 2)
        let last = board.candles[1]
        XCTAssertEqual(last.open, 2.0 / 3.0, accuracy: 1e-12)
        XCTAssertEqual(last.high, 7.0 / 6.0, accuracy: 1e-12)
        XCTAssertEqual(last.low, 0.5, accuracy: 1e-12)
        XCTAssertEqual(last.close, 1.0, accuracy: 1e-12)
        // 组合最新收盘 = 今收（归一化基准），当日涨跌 = 相对前一根。
        XCTAssertEqual(board.lastDate, d2)
        XCTAssertEqual(board.dayChange ?? 0, board.candles[1].close / board.candles[0].close - 1,
                       accuracy: 1e-12)
    }

    /// 基金没有日内价：开 = 昨收、收 = 今收、高/低 = 两者较大/较小（没有影线）。
    func testFundWithoutIntradayFallsBackToPreviousClose() {
        let fund = FocusSeries(assetKey: "DEMO-B", name: "示例基金", weight: 1, bars: [
            bar("DEMO-B", d1, 2),
            bar("DEMO-B", d2, 3),
        ])
        let candle = FocusComposer.compose(series: [fund]).candles[1]
        XCTAssertEqual(candle.open, 2.0 / 3.0, accuracy: 1e-12)
        XCTAssertEqual(candle.close, 1.0, accuracy: 1e-12)
        XCTAssertEqual(candle.high, 1.0, accuracy: 1e-12)
        XCTAssertEqual(candle.low, 2.0 / 3.0, accuracy: 1e-12)
        // 无日内价 = 没有影线：高/低恰好等于实体的两端。
        XCTAssertEqual(candle.high, max(candle.open, candle.close), accuracy: 1e-12)
        XCTAssertEqual(candle.low, min(candle.open, candle.close), accuracy: 1e-12)
    }

    /// 不等权 0.7 / 0.3 的逐项加权。
    func testUnequalWeightsWeightEachFieldSeparately() {
        let stock = FocusSeries(assetKey: "DEMO-A", name: "示例股票", weight: 0.7, bars: [
            bar("DEMO-A", d1, 3, open: 3, high: 3, low: 3),
            bar("DEMO-A", d2, 3, open: 1.5, high: 3, low: 1.5),
        ])
        let fund = FocusSeries(assetKey: "DEMO-B", name: "示例基金", weight: 0.3, bars: [
            bar("DEMO-B", d1, 2),
            bar("DEMO-B", d2, 3),
        ])
        let candle = FocusComposer.compose(series: [stock, fund]).candles[1]
        XCTAssertEqual(candle.open, 0.55, accuracy: 1e-12)
        XCTAssertEqual(candle.high, 1.0, accuracy: 1e-12)
        XCTAssertEqual(candle.low, 0.55, accuracy: 1e-12)
        XCTAssertEqual(candle.close, 1.0, accuracy: 1e-12)
    }

    /// 某资产当日休市/无数据 → 平盘，不产生假影线。
    func testMissingDayIsFlatAndAddsNoWick() {
        let a = FocusSeries(assetKey: "DEMO-A", name: "示例A", weight: 1, bars: [
            bar("DEMO-A", d1, 2), bar("DEMO-A", "2026-10-26", 4),
        ])
        let b = FocusSeries(assetKey: "DEMO-B", name: "示例B", weight: 1, bars: [
            bar("DEMO-B", d1, 2), bar("DEMO-B", d2, 2), bar("DEMO-B", "2026-10-26", 2),
        ])
        let board = FocusComposer.compose(series: [a, b])
        let gap = try? XCTUnwrap(board.candles.first { $0.date == d2 })
        XCTAssertEqual(gap?.open ?? 0, 0.75, accuracy: 1e-12)
        XCTAssertEqual(gap?.high ?? 0, 0.75, accuracy: 1e-12)
        XCTAssertEqual(gap?.low ?? 0, 0.75, accuracy: 1e-12)
        XCTAssertEqual(gap?.close ?? 0, 0.75, accuracy: 1e-12)
    }

    /// 只取最后 20 根；历史不足时按实际根数输出（不补假数据）。
    func testCapsAtTwentyCandlesAndKeepsShortHistory() {
        var bars: [PricePoint] = []
        for i in 0..<25 {
            bars.append(bar("DEMO-A", String(format: "2026-09-%02d", i + 1), 10 + Double(i)))
        }
        let long = FocusComposer.compose(series: [
            FocusSeries(assetKey: "DEMO-A", name: "示例A", weight: 1, bars: bars)
        ])
        XCTAssertEqual(long.candles.count, FocusComposer.defaultCandleCount)
        XCTAssertEqual(long.candles.last?.date, bars.last?.date)

        let short = FocusComposer.compose(series: [
            FocusSeries(assetKey: "DEMO-A", name: "示例A", weight: 1, bars: Array(bars.prefix(3)))
        ])
        XCTAssertEqual(short.candles.count, 3)
    }

    /// 极值：当日 = 各自最近两个观测点；并列按权重降序取第一。
    func testExtremesUseEachAssetsOwnLastTwoObservations() {
        let up = FocusSeries(assetKey: "DEMO-A", name: "涨", weight: 1, bars: [
            bar("DEMO-A", d1, 1), bar("DEMO-A", d2, 1.02),
        ])
        let flat = FocusSeries(assetKey: "DEMO-B", name: "平", weight: 1, bars: [
            bar("DEMO-B", d1, 1), bar("DEMO-B", d2, 0.99),
        ])
        let down = FocusSeries(assetKey: "DEMO-C", name: "跌", weight: 1, bars: [
            bar("DEMO-C", d1, 1), bar("DEMO-C", d2, 0.97),
        ])
        let board = FocusComposer.compose(series: [up, flat, down])
        XCTAssertEqual(board.gainer?.assetKey, "DEMO-A")
        XCTAssertEqual(board.loser?.assetKey, "DEMO-C")
        XCTAssertEqual(board.gainer?.dayChange ?? 0, 0.02, accuracy: 1e-12)
        XCTAssertEqual(board.gainer?.candles.count, 2)
        XCTAssertEqual(board.gainer?.lastDate, d2)

        // 并列（都是 +1%）：权重大的排前面。
        let light = FocusSeries(assetKey: "DEMO-D", name: "轻", weight: 1, bars: [
            bar("DEMO-D", d1, 1), bar("DEMO-D", d2, 1.01),
        ])
        let heavy = FocusSeries(assetKey: "DEMO-E", name: "重", weight: 9, bars: [
            bar("DEMO-E", d1, 1), bar("DEMO-E", d2, 1.01),
        ])
        let tied = FocusComposer.compose(series: [light, heavy])
        XCTAssertEqual(tied.gainer?.assetKey, "DEMO-E")
    }

    /// 标的首根 bar 之前的日期不参与合成（权重按当天参与者归一），
    /// 不能拿"未来的收盘"往前填出平盘。
    func testAssetDoesNotContributeBeforeItsFirstBar() {
        let late = FocusSeries(assetKey: "DEMO-A", name: "后加的标的", weight: 9, bars: [
            bar("DEMO-A", "2026-10-23", 5, open: 5, high: 5, low: 5),
            bar("DEMO-A", "2026-10-26", 5, open: 5, high: 6, low: 4),
        ])
        let normal = FocusSeries(assetKey: "DEMO-B", name: "老标的", weight: 1, bars: [
            bar("DEMO-B", "2026-10-21", 2), bar("DEMO-B", "2026-10-22", 2),
            bar("DEMO-B", "2026-10-23", 2), bar("DEMO-B", "2026-10-26", 2),
        ])
        let board = FocusComposer.compose(series: [late, normal])
        XCTAssertEqual(board.candles.count, 4)
        // 前三天只有老标的（权重 1）→ 收盘恒 1.0，与后加标的的 9 倍权重无关。
        for candle in board.candles.prefix(3) {
            XCTAssertEqual(candle.close, 1.0, accuracy: 1e-12)
            XCTAssertEqual(candle.high, candle.low, accuracy: 1e-12)
        }
    }

    /// 只有 1 个价格点的持仓被排除，权重在剩余持仓间重新归一。
    func testSingleBarAssetIsExcludedFromComposition() {
        let single = FocusSeries(assetKey: "DEMO-A", name: "新标的", weight: 100, bars: [
            bar("DEMO-A", "2026-10-26", 9),
        ])
        let normal = FocusSeries(assetKey: "DEMO-B", name: "老标的", weight: 50, bars: [
            bar("DEMO-B", d1, 2), bar("DEMO-B", d2, 2), bar("DEMO-B", "2026-10-26", 4),
        ])
        let board = FocusComposer.compose(series: [single, normal])
        XCTAssertEqual(board.candles.count, 3)
        // 只反映老标的：2/4 = 0.5、2/4 = 0.5、4/4 = 1。
        XCTAssertEqual(board.candles[0].close, 0.5, accuracy: 1e-12)
        XCTAssertEqual(board.candles[1].close, 0.5, accuracy: 1e-12)
        XCTAssertEqual(board.candles[2].close, 1.0, accuracy: 1e-12)
        XCTAssertNil(board.loser)   // 只有一个可比标的 → 不给跌幅卡
    }
}
