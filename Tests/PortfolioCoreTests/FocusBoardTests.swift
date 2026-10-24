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
                     open: Double? = nil, high: Double? = nil, low: Double? = nil,
                     raw: Double? = nil) -> PricePoint {
        PricePoint(assetKey: key, date: date, close: close,
                   rawClose: raw, open: open, high: high, low: low)
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

    // MARK: - 极值卡的真实现价（Schema v10 raw_close）

    /// 价格蜡烛 = 入库 O/H/L × (rawClose / close)：复权价 ×100 → 真实价格。
    func testPickPriceCandlesRescaleToRealPrices() throws {
        let s = FocusSeries(assetKey: "DEMO-A", name: "示例权益", weight: 1, bars: [
            bar("DEMO-A", d1, 2.4, open: 2.3, high: 2.5, low: 2.2, raw: 240),
            bar("DEMO-A", d2, 2.5, open: 2.45, high: 2.6, low: 2.4, raw: 250),
        ])
        let board = FocusComposer.compose(series: [s])
        let price = try XCTUnwrap(board.gainer?.price)
        XCTAssertEqual(price.currency, "USD")
        XCTAssertEqual(price.candles.count, 2)
        let last = price.candles[1]
        XCTAssertEqual(last.close, 250, accuracy: 1e-9)
        XCTAssertEqual(last.open, 245, accuracy: 1e-9)
        XCTAssertEqual(last.high, 260, accuracy: 1e-9)
        XCTAssertEqual(last.low, 240, accuracy: 1e-9)
        // 归一化序列仍在（未回补时视图用它），且不受原始价影响。
        XCTAssertEqual(board.gainer?.candles[1].close ?? 0, 1.0, accuracy: 1e-12)
    }

    /// 基金：rawClose = 单位净值；无日内价 → 开 = 上一根真实收盘、高/低 = 实体两端（无假影线）。
    /// 顶部涨跌幅仍是总回报口径（累计净值），除息日两者会背离 —— 这是预期行为。
    func testFundPriceCandlesUseUnitNavAndPreviousClose() throws {
        let fund = FocusSeries(assetKey: "DEMO-B", name: "示例基金", weight: 1, bars: [
            bar("DEMO-B", d1, 2.0, raw: 1.50),
            bar("DEMO-B", d2, 2.0, raw: 1.48),   // 累计净值不变（分红再投），份额净值下跌
        ])
        let board = FocusComposer.compose(series: [fund])
        let price = try XCTUnwrap(board.gainer?.price)
        let last = try XCTUnwrap(price.candles.last)
        XCTAssertEqual(last.close, 1.48, accuracy: 1e-12)
        XCTAssertEqual(last.open, 1.50, accuracy: 1e-12)
        XCTAssertEqual(last.high, 1.50, accuracy: 1e-12)
        XCTAssertEqual(last.low, 1.48, accuracy: 1e-12)
        XCTAssertEqual(board.gainer?.dayChange ?? 9, 0, accuracy: 1e-12)
    }

    /// 全有或全无：窗口内任一根缺原始价 → 整条价格序列为 nil（不混画两种口径）。
    func testPickPriceIsNilWhenAnyBarLacksRawClose() {
        let s = FocusSeries(assetKey: "DEMO-A", name: "示例权益", weight: 1, bars: [
            bar("DEMO-A", d1, 2.0),
            bar("DEMO-A", d2, 2.1, raw: 210),
        ])
        let board = FocusComposer.compose(series: [s])
        XCTAssertNil(board.gainer?.price)
    }

    /// 只取最后 20 根；窗口首根的「开」= 窗口外那一根的真实收盘（不是自己的收盘）。
    func testPriceWindowCapsAtTwentyAndSeedsFirstOpenFromPriorClose() throws {
        var bars: [PricePoint] = []
        for i in 0..<25 {
            bars.append(bar("DEMO-A", String(format: "2026-09-%02d", i + 1),
                            10 + Double(i), raw: 100 + Double(i)))
        }
        let board = FocusComposer.compose(series: [
            FocusSeries(assetKey: "DEMO-A", name: "示例权益", weight: 1, bars: bars)
        ])
        let price = try XCTUnwrap(board.gainer?.price)
        XCTAssertEqual(price.candles.count, FocusComposer.defaultCandleCount)
        // 窗口 = 索引 5…24 的 20 根；索引 4 的真实收盘 = 104 作首根的开。
        XCTAssertEqual(price.candles.first?.open ?? 0, 104, accuracy: 1e-12)
        XCTAssertEqual(price.candles.last?.close ?? 0, 124, accuracy: 1e-12)
    }

    /// 原始价不参与合成：给不给 rawClose，合成蜡烛必须逐位相同。
    func testRawCloseDoesNotAffectComposition() {
        let without = FocusSeries(assetKey: "DEMO-A", name: "示例权益", weight: 1, bars: [
            bar("DEMO-A", d1, 2.0, open: 1.9, high: 2.1, low: 1.8),
            bar("DEMO-A", d2, 2.2, open: 2.0, high: 2.3, low: 1.95),
        ])
        let with = FocusSeries(assetKey: "DEMO-A", name: "示例权益", weight: 1, bars: [
            bar("DEMO-A", d1, 2.0, open: 1.9, high: 2.1, low: 1.8, raw: 200),
            bar("DEMO-A", d2, 2.2, open: 2.0, high: 2.3, low: 1.95, raw: 220),
        ])
        XCTAssertEqual(FocusComposer.compose(series: [without]).candles,
                       FocusComposer.compose(series: [with]).candles)
    }
}
