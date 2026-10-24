import XCTest
@testable import PortfolioCore

/// 东财（pingzhongdata）时间戳取日口径。
///
/// 东财 `x` 是**北京时间 00:00** 的毫秒时间戳。按 UTC 取日会整体早一天 ——
/// 与 Yahoo 序列配对时会把日期标签和相关性都带偏（Python 侧同一根因已于 0.6-alpha.2 修复）。
/// 这里锁定 Swift 侧同款口径：一律按 `Asia/Shanghai` 取日。
///
/// 全部使用虚构净值与时间戳，不含任何真实持仓信息。
final class EastmoneySourceTests: XCTestCase {
    /// 2023-01-03 00:00:00 +08:00 == 2023-01-02 16:00:00 UTC（北京时间零点）。
    private let beijingMidnight: Int64 = 1_672_675_200_000
    /// 2023-01-04 00:00:00 +08:00。
    private let nextBeijingMidnight: Int64 = 1_672_761_600_000

    // MARK: - DateUtil 的时间戳取日

    func testDateStringHonoursExplicitTimeZone() {
        XCTAssertEqual(
            DateUtil.dateString(fromTimestampMillis: beijingMidnight, timeZone: "Asia/Shanghai"),
            "2023-01-03"
        )
        // 同一时间戳按 UTC 会落到前一天 —— 这是错位口径，仅作对照，不代表期望行为。
        XCTAssertEqual(DateUtil.dateString(fromTimestampMillis: beijingMidnight), "2023-01-02")
    }

    // MARK: - 东财净值解析（单位净值 / 累计净值两条路径）

    func testParseNavUnitNavUsesAsiaShanghai() {
        let js = #"var Data_netWorthTrend = [{"x":1672675200000,"y":1.5},{"x":1672761600000,"y":1.6}];"#
        let rows = EastmoneySource.parseNav(js, cumulative: false)
        XCTAssertEqual(rows.map { $0.date }, ["2023-01-03", "2023-01-04"])
        XCTAssertEqual(rows.map { $0.value }, [1.5, 1.6])
    }

    func testParseNavCumulativeNavUsesAsiaShanghai() {
        // 累计净值优先取 Data_ACWorthTrend（[timestamp, value] 数组格式）。
        let js = #"var Data_ACWorthTrend = [[1672675200000,2.5],[1672761600000,2.6]]; var Data_netWorthTrend = [{"x":1672675200000,"y":1.5}];"#
        let rows = EastmoneySource.parseNav(js, cumulative: true)
        XCTAssertEqual(rows.map { $0.date }, ["2023-01-03", "2023-01-04"])
        XCTAssertEqual(rows.map { $0.value }, [2.5, 2.6])
    }

    func testParseNavFallsBackToUnitNavWhenCumulativeMissing() {
        // 累计净值缺失时回退到单位净值，但日期口径必须仍是北京时间。
        let js = #"var Data_netWorthTrend = [{"x":1672675200000,"y":1.5}];"#
        let rows = EastmoneySource.parseNav(js, cumulative: true)
        XCTAssertEqual(rows.count, 1)
        XCTAssertEqual(rows.first?.date, "2023-01-03")
        XCTAssertEqual(rows.first?.value, 1.5)
        XCTAssertEqual(nextBeijingMidnight - beijingMidnight, 86_400_000)
    }

    // MARK: - 历史序列的真实现价（单位净值）

    /// close 仍是累计净值（总回报口径），rawClose 才是份额的真实现价。
    func testHistoryPointsCarryUnitNavAsRawClose() {
        let js = #"var Data_ACWorthTrend = [[1672675200000,2.5],[1672761600000,2.6]]; var Data_netWorthTrend = [{"x":1672675200000,"y":1.5},{"x":1672761600000,"y":1.6}];"#
        let rows = EastmoneySource.historyPoints(from: js, symbol: "FICT")
        XCTAssertEqual(rows.map { $0.close }, [2.5, 2.6])
        XCTAssertEqual(rows.map { $0.rawClose ?? 0 }, [1.5, 1.6])
        XCTAssertEqual(rows.map { $0.currency }, ["CNY", "CNY"])
    }

    /// 没有单位净值 → rawClose 留 nil，**不拿累计净值冒充真实现价**。
    func testHistoryPointsLeaveRawCloseNilWhenUnitNavMissing() {
        let js = #"var Data_ACWorthTrend = [[1672675200000,2.5]];"#
        let rows = EastmoneySource.historyPoints(from: js, symbol: "FICT")
        XCTAssertEqual(rows.count, 1)
        XCTAssertEqual(rows.first?.close, 2.5)
        XCTAssertNil(rows.first?.rawClose)
    }

    /// 单位净值缺某一天 → 只有那一行的 rawClose 为 nil，其余照常按日期配对。
    func testHistoryPointsPairRawCloseByDate() {
        let js = #"var Data_ACWorthTrend = [[1672675200000,2.5],[1672761600000,2.6]]; var Data_netWorthTrend = [{"x":1672761600000,"y":1.6}];"#
        let rows = EastmoneySource.historyPoints(from: js, symbol: "FICT")
        XCTAssertEqual(rows.count, 2)
        XCTAssertNil(rows[0].rawClose)
        XCTAssertEqual(rows[1].rawClose ?? 0, 1.6)
    }
}
