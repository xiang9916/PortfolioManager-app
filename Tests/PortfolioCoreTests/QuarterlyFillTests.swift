import XCTest
@testable import PortfolioCore

/// 「总市值 / 总成本」一键填入的口径测试（见 docs/adr/0001）。
///
/// 这条规则决定往用户的历史底稿里写什么数，视图层无法在 CI 里点击验证，所以规则本体
/// 放在 PortfolioCore 并由这里锁住。
final class QuarterlyFillTests: XCTestCase {
    private func alloc(value: Double, cost: Double) -> AllocationSnapshot {
        AllocationSnapshot(asOfDate: nil, totalValue: value, domesticValue: 0, overseasValue: 0,
                           totalCost: cost, domesticCost: 0, overseasCost: 0, slices: [])
    }

    /// 最早的一列 = 期初基准列，最晚的一列 = 最新季度。
    private var reports: [QuarterlyReport] {
        [QuarterlyReport(periodEnd: "2023-12-31"), QuarterlyReport(periodEnd: "2026-06-30")]
    }

    func testFillsCurrentTotalValueAndCost() {
        let a = alloc(value: 471_416.31, cost: 474_203.68)
        XCTAssertEqual(QuarterlyFill.value(field: .marketValue, periodEnd: "2026-06-30",
                                           reports: reports, allocation: a) ?? 0,
                       471_416.31, accuracy: 1e-9)
        XCTAssertEqual(QuarterlyFill.value(field: .totalCost, periodEnd: "2026-06-30",
                                           reports: reports, allocation: a) ?? 0,
                       474_203.68, accuracy: 1e-9)
    }

    /// 期初基准列：语义是「起点：总市值 = 总成本」，两格都填总成本。
    func testBaselineColumnFillsCostForBothFields() {
        let a = alloc(value: 471_416.31, cost: 474_203.68)
        XCTAssertEqual(QuarterlyFill.value(field: .marketValue, periodEnd: "2023-12-31",
                                           reports: reports, allocation: a) ?? 0,
                       474_203.68, accuracy: 1e-9)
        XCTAssertEqual(QuarterlyFill.value(field: .totalCost, periodEnd: "2023-12-31",
                                           reports: reports, allocation: a) ?? 0,
                       474_203.68, accuracy: 1e-9)
    }

    /// 只有「总市值 / 总成本」两格可填；其余 7 个是期间现金流。
    func testOnlyTheTwoAssetFieldsAreFillable() {
        let a = alloc(value: 100, cost: 200)
        for f in QuarterlyField.allCases where f != .marketValue && f != .totalCost {
            XCTAssertNil(QuarterlyFill.value(field: f, periodEnd: "2026-06-30",
                                             reports: reports, allocation: a), "\(f)")
        }
    }

    /// 没有数据时不填（按钮不出现），绝不要把 0 写进底稿。
    func testNoValueMeansNoButton() {
        XCTAssertNil(QuarterlyFill.value(field: .marketValue, periodEnd: "2026-06-30",
                                         reports: reports, allocation: nil))
        let zero = alloc(value: 0, cost: 0)
        XCTAssertNil(QuarterlyFill.value(field: .marketValue, periodEnd: "2026-06-30",
                                         reports: reports, allocation: zero))
        XCTAssertNil(QuarterlyFill.value(field: .totalCost, periodEnd: "2026-06-30",
                                         reports: reports, allocation: zero))
        // 期初列同理
        XCTAssertNil(QuarterlyFill.value(field: .marketValue, periodEnd: "2023-12-31",
                                         reports: reports, allocation: zero))
    }

    /// 只有一个季度时，它既是期初也是最新 → 期望按**期初**语义填总成本（图例已写死这条约定）。
    func testSingleColumnIsTreatedAsBaseline() {
        let only = [QuarterlyReport(periodEnd: "2026-09-30")]
        let a = alloc(value: 471_416, cost: 474_203)
        XCTAssertEqual(QuarterlyFill.value(field: .marketValue, periodEnd: "2026-09-30",
                                           reports: only, allocation: a) ?? 0,
                       474_203, accuracy: 1e-9)
    }
}
