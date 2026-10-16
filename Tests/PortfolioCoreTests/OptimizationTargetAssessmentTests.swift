import XCTest
@testable import PortfolioCore

/// 「实际收益 vs 目标下限」判定的单测（Core 下沉后的唯一容差来源）。
///
/// fixture 全部是**虚构**收益率（8% / 10% 这类编造值），不含任何真实持仓、金额或标的代码。
final class OptimizationTargetAssessmentTests: XCTestCase {

    private let tol = OptimizationTargetAssessment.tolerance

    /// 用「已报告差额」构造，避免减法引入浮点误差 —— 边界用例必须精确。
    private func assessment(gap: Double, target: Double = 0.10) -> OptimizationTargetAssessment {
        OptimizationTargetAssessment(deliveredReturn: target + gap, targetReturn: target, reportedGap: gap)
    }

    // MARK: - 达成 / 未达成

    func testExcessOverTargetIsMet() {
        let a = OptimizationTargetAssessment(deliveredReturn: 0.12, targetReturn: 0.10)
        XCTAssertEqual(a.gap, 0.02, accuracy: 1e-12)
        XCTAssertTrue(a.met)
        XCTAssertEqual(a.severity, .none)
        XCTAssertEqual(a.shortfall, 0)
        XCTAssertEqual(a.excess, 0.02, accuracy: 1e-12)
        XCTAssertEqual(a.tolerance, 1e-4)
    }

    func testGapBeyondToleranceIsNotMet() {
        let a = assessment(gap: -1.5 * tol)
        XCTAssertFalse(a.met)
        XCTAssertEqual(a.gap, -1.5 * tol, accuracy: 1e-18)
        XCTAssertEqual(a.shortfall, 1.5 * tol, accuracy: 1e-18)
        XCTAssertEqual(a.excess, 0)
        XCTAssertEqual(a.severity, .marginal)
    }

    // MARK: - 浮点边界（±1e-9 / 恰好等于容差）

    func testGapMinusOneNanoIsMet() {
        let a = assessment(gap: -1e-9)
        XCTAssertTrue(a.met)
        XCTAssertEqual(a.severity, .none)
        XCTAssertEqual(a.shortfall, 0)
        XCTAssertEqual(a.gap, -1e-9, accuracy: 1e-18)
    }

    func testGapPlusOneNanoIsMet() {
        let a = assessment(gap: 1e-9)
        XCTAssertTrue(a.met)
        XCTAssertEqual(a.severity, .none)
        XCTAssertEqual(a.shortfall, 0)
        XCTAssertEqual(a.excess, 1e-9, accuracy: 1e-18)
    }

    func testExactToleranceBoundaryIsMet() {
        // 判定是 gap >= -tolerance：恰好 -1e-4 仍算达成。
        let a = assessment(gap: -tol)
        XCTAssertTrue(a.met)
        XCTAssertEqual(a.severity, .none)
        XCTAssertEqual(a.shortfall, 0)
    }

    // MARK: - 低于目标的严重度分档

    func testSeverityTiers() {
        let cases: [(shortfall: Double, met: Bool, severity: OptimizationTargetAssessment.ShortfallSeverity)] = [
            (0,            true,  .none),
            (tol,          true,  .none),
            (2 * tol,      false, .marginal),
            (1e-3,         false, .marginal),   // 上界含端点
            (2e-3,         false, .material),
            (5e-3,         false, .material),   // 上界含端点
            (5.1e-3,       false, .severe),
            (2e-2,         false, .severe),
        ]
        for c in cases {
            let a = assessment(gap: -c.shortfall)
            XCTAssertEqual(a.met, c.met, "shortfall = \(c.shortfall)")
            XCTAssertEqual(a.severity, c.severity, "shortfall = \(c.shortfall)")
            XCTAssertEqual(a.shortfall, c.met ? 0 : c.shortfall, accuracy: 1e-18, "shortfall = \(c.shortfall)")
        }
    }

    // MARK: - 已报告差额优先 / 非有限入参

    func testReportedGapTakesPrecedence() {
        let a = OptimizationTargetAssessment(deliveredReturn: 0.10, targetReturn: 0.10, reportedGap: 0.05)
        XCTAssertEqual(a.gap, 0.05, accuracy: 1e-12)
        XCTAssertTrue(a.met)
    }

    func testNonFiniteReportedGapFallsBackToComputedGap() {
        let a = OptimizationTargetAssessment(deliveredReturn: 0.11, targetReturn: 0.10, reportedGap: .nan)
        XCTAssertEqual(a.gap, 0.01, accuracy: 1e-12)
        XCTAssertTrue(a.met)
    }

    func testNonFiniteInputsAreIndeterminateAndNotMet() {
        let nan = OptimizationTargetAssessment(deliveredReturn: .nan, targetReturn: 0.10)
        XCTAssertFalse(nan.met)
        XCTAssertEqual(nan.severity, .indeterminate)
        XCTAssertEqual(nan.shortfall, 0)
        XCTAssertEqual(nan.excess, 0)

        let inf = OptimizationTargetAssessment(deliveredReturn: 0.10, targetReturn: .infinity)
        XCTAssertFalse(inf.met)
        XCTAssertEqual(inf.severity, .indeterminate)
    }

    // MARK: - 结果模型上的便利判定

    func testResultConvenienceUsesComputedGapWhenMissing() {
        let result = OptimizationResult(
            generatedAt: nil,
            portfolio: PortfolioSummary(expectedReturn: 0.09, volatility: 0.10, sharpe: 0.9, worstYear95: -0.05),
            assets: [],
            benchmark: nil,
            testAssets: nil,
            warnings: nil,
            targetReturn: 0.10,
            targetGap: nil,
            warningItems: nil,
            objective: nil,
            sourceDetail: nil)
        let a = result.targetAssessment
        XCTAssertNotNil(a)
        XCTAssertEqual(a?.gap ?? .nan, -0.01, accuracy: 1e-12)
        XCTAssertFalse(a?.met ?? true)
        XCTAssertEqual(a?.severity, .severe)
    }

    func testResultConvenienceIsNilWithoutTarget() {
        let result = OptimizationResult(
            generatedAt: nil,
            portfolio: PortfolioSummary(expectedReturn: 0.09, volatility: 0.10, sharpe: 0.9, worstYear95: -0.05),
            assets: [],
            benchmark: nil,
            testAssets: nil,
            warnings: nil,
            targetReturn: nil,
            targetGap: nil,
            warningItems: nil,
            objective: nil,
            sourceDetail: nil)
        XCTAssertNil(result.targetAssessment)
    }
}
