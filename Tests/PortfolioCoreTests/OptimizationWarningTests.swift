import XCTest
@testable import PortfolioCore

/// 优化器告警分类（code 路径 + 旧输出文案兜底）的单测。
///
/// 所有 fixture 都是**虚构**的收益率/文案，不含任何真实持仓、权重、金额或标的代码。
final class OptimizationWarningTests: XCTestCase {

    /// 镜像 Optimizer/scripts/optimize_portfolio.py 的 WARNING_CODES（2026-10-18 快照，22 个）。
    private let expectedCodes: [String] = [
        "target.stage1_fallback",
        "target.infeasible",
        "target.budget_exhausted",
        "target.sleeve_unreachable",
        "sleeve.not_converged",
        "sleeve.undeliverable",
        "sleeve.floor_infeasible",
        "sleeve.floor_postcheck_failed",
        "sleeve.levels_lifted",
        "sleeve.excluded_no_basket",
        "sleeve.stage2_error",
        "sleeve.us_unavailable",
        "active_cap.pool_conflict",
        "active_cap.hit",
        "summary.missing_weights",
        "summary.weight_sum_mismatch",
        "fund.available_none",
        "test.ticker_skipped",
        "held.skipped",
        "held.us_equity_outside_catalog",
        "sensitivity.anchor_mismatch",
        "other",
    ]

    // MARK: - code 覆盖与严重度映射

    func testEveryOptimizerCodeHasACase() {
        XCTAssertEqual(OptimizationWarningKind.allCodes.count, expectedCodes.count)
        XCTAssertEqual(Set(OptimizationWarningKind.allCodes), Set(expectedCodes))
    }

    func testSeverityMapping() {
        let expected: [String: OptimizationWarningSeverity] = [
            "target.stage1_fallback": .critical,
            "target.infeasible": .critical,
            "target.budget_exhausted": .critical,
            "target.sleeve_unreachable": .critical,
            "sleeve.undeliverable": .critical,
            "sleeve.floor_infeasible": .critical,
            "sleeve.floor_postcheck_failed": .critical,
            "sleeve.us_unavailable": .critical,
            "sleeve.not_converged": .warning,
            "sleeve.excluded_no_basket": .warning,
            "sleeve.stage2_error": .warning,
            "active_cap.pool_conflict": .warning,
            "active_cap.hit": .warning,
            "summary.missing_weights": .critical,
            "summary.weight_sum_mismatch": .critical,
            "fund.available_none": .warning,
            "held.skipped": .warning,
            "held.us_equity_outside_catalog": .warning,
            "sensitivity.anchor_mismatch": .warning,
            "other": .warning,
            "sleeve.levels_lifted": .info,
            "test.ticker_skipped": .info,
        ]
        for code in expectedCodes {
            guard let kind = OptimizationWarningKind(rawValue: code) else {
                XCTFail("缺 case: \(code)"); continue
            }
            XCTAssertEqual(kind.severity, expected[code], "code = \(code)")
        }
        XCTAssertEqual(expected.count, expectedCodes.count)
    }

    // MARK: - 新路径：只认 code，不看文案

    func testCodePathIgnoresMessageText() {
        // 文案里塞满旧关键词，code 说它是 info → 必须按 code 判成 info。
        let w = OptimizationWarningClassifier.classify(
            code: "test.ticker_skipped",
            message: "目标不可行 / 低于目标 / 预算已用尽 / 已回退到最大可行收益")
        XCTAssertEqual(w.kind, .testTickerSkipped)
        XCTAssertEqual(w.severity, .info)
        XCTAssertTrue(w.classifiedByCode)
        XCTAssertEqual(w.code, "test.ticker_skipped")
    }

    func testCodePathClassifiesCriticalWithoutKeywordInText() {
        // 文案不含任何旧关键词，code 说它 critical → 必须 critical（旧关键词路径会漏报）。
        let w = OptimizationWarningClassifier.classify(
            code: "target.sleeve_unreachable",
            message: "袖内收益下限无法建立：目标高于可达水平。")
        XCTAssertEqual(w.kind, .targetSleeveUnreachable)
        XCTAssertEqual(w.severity, .critical)
        XCTAssertTrue(w.classifiedByCode)
    }

    func testLevelsLiftedIsInfoNotCritical() {
        // 交付未降级（组合层复核后按请求目标重解）→ info；旧关键词会因"不可行"三字误报成 critical。
        let w = OptimizationWarningClassifier.classify(
            code: "sleeve.levels_lifted",
            message: "目标在阶段一的当前假设下不可行，但组合层复核显示可达。")
        XCTAssertEqual(w.severity, .info)
    }

    func testCapHitAndSummarySelfChecks() {
        // active 触顶：约束生效，交付仍有效 → warning。
        let hit = OptimizationWarningClassifier.classify(
            code: "active_cap.hit", message: "上限 10% 生效：DEMO1 10.00%")
        XCTAssertEqual(hit.kind, .activeCapHit)
        XCTAssertEqual(hit.severity, .warning)
        XCTAssertTrue(hit.classifiedByCode)

        // 汇总自检失败：结果清单不自洽，照它执行会出错 → critical（旧关键词路径会漏报）。
        let missing = OptimizationWarningClassifier.classify(
            code: "summary.missing_weights", message: "自检：1 个有权重的标的未出现在结果清单里。")
        XCTAssertEqual(missing.kind, .summaryMissingWeights)
        XCTAssertEqual(missing.severity, .critical)
        XCTAssertTrue(missing.classifiedByCode)

        let mismatch = OptimizationWarningClassifier.classify(
            code: "summary.weight_sum_mismatch", message: "自检：结果清单权重合计 0.9998 ≠ 1。")
        XCTAssertEqual(mismatch.kind, .summaryWeightSumMismatch)
        XCTAssertEqual(mismatch.severity, .critical)
        XCTAssertTrue(mismatch.classifiedByCode)
    }

    // MARK: - 未知 code / 旧输出兜底

    func testUnknownCodeFallsBackToOtherWithoutKeywords() {
        let w = OptimizationWarningClassifier.classify(code: "brand.new_code", message: "口径说明")
        XCTAssertEqual(w.kind, .other)
        XCTAssertEqual(w.severity, .warning)
        XCTAssertFalse(w.classifiedByCode)
        XCTAssertEqual(w.code, "brand.new_code")
    }

    func testUnknownCodeWithLegacyKeywordsEscalatesToCritical() {
        let w = OptimizationWarningClassifier.classify(code: "brand.new_code", message: "目标不可行，已回退")
        XCTAssertEqual(w.kind, .other)
        XCTAssertEqual(w.severity, .critical)
        XCTAssertFalse(w.classifiedByCode)
    }

    func testLegacyTextPathClassifiesByKeywords() {
        let critical = OptimizationWarningClassifier.classify(
            code: nil, message: "美国权益内部优化不可行，组合统计已回退使用第一阶段结果。")
        XCTAssertEqual(critical.kind, .other)
        XCTAssertEqual(critical.severity, .critical)
        XCTAssertFalse(critical.classifiedByCode)
        XCTAssertNil(critical.code)

        let benign = OptimizationWarningClassifier.classify(
            code: nil, message: "袖内参数迭代 3 轮未完全收敛（最后一轮最大变化 0.0031）。")
        XCTAssertEqual(benign.severity, .warning)

        let skipped = OptimizationWarningClassifier.classify(
            code: nil, message: "测试标的 DEMO1 已跳过: 无数据")
        XCTAssertEqual(skipped.severity, .warning)
    }

    func testLegacyKeywordListMirrorsResultView() {
        XCTAssertEqual(OptimizationWarningClassifier.legacyCriticalKeywords,
                       ["不可行", "不可交付", "已回退", "回退到", "回退使用", "回退/不可行",
                        "低于目标", "预算已用尽", "预算未用尽"])
    }

    // MARK: - OptimizationResult 消费

    private func makeResult(warnings: [String]?, items: [OptimizationWarningItem]?) -> OptimizationResult {
        OptimizationResult(
            generatedAt: nil,
            portfolio: PortfolioSummary(expectedReturn: 0.09, volatility: 0.10, sharpe: 0.9, worstYear95: -0.05),
            assets: [],
            benchmark: nil,
            testAssets: nil,
            warnings: warnings,
            targetReturn: 0.10,
            targetGap: -0.01,
            warningItems: items,
            objective: nil,
            sourceDetail: nil)
    }

    func testClassifiedWarningsPrefersWarningItems() {
        let result = makeResult(
            warnings: ["旧文案：目标不可行，已回退"],
            items: [OptimizationWarningItem(code: "test.ticker_skipped", message: "新文案：测试标的已跳过")])
        XCTAssertEqual(result.classifiedWarnings.count, 1)
        XCTAssertEqual(result.classifiedWarnings.first?.severity, .info)
        XCTAssertTrue(result.classifiedWarnings.first?.classifiedByCode ?? false)
        XCTAssertFalse(result.hasCriticalWarnings)
    }

    func testClassifiedWarningsFallsBackWhenItemsEmpty() {
        let result = makeResult(warnings: ["目标不可行，已回退到最大可行收益 6%。"], items: [])
        XCTAssertEqual(result.classifiedWarnings.count, 1)
        XCTAssertEqual(result.classifiedWarnings.first?.severity, .critical)
        XCTAssertFalse(result.classifiedWarnings.first?.classifiedByCode ?? true)
        XCTAssertTrue(result.hasCriticalWarnings)
    }

    func testClassifiedWarningsEmptyWithoutAnyWarnings() {
        XCTAssertTrue(makeResult(warnings: nil, items: nil).classifiedWarnings.isEmpty)
    }

    // MARK: - 结果 JSON 解码（旧输出无 warning_items / 新输出有）

    private func decode(_ json: String) throws -> OptimizationResult {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(OptimizationResult.self, from: Data(json.utf8))
    }

    private let baseJSON = """
    {"portfolio": {"expected_return": 0.09, "volatility": 0.10, "sharpe": 0.9, "worst_year95": -0.05},
     "assets": [],
     "target_return": 0.10, "target_gap": -0.01}
    """

    func testDecodeLegacyJSONWithoutWarningItems() throws {
        let json = """
        {"portfolio": {"expected_return": 0.09, "volatility": 0.10, "sharpe": 0.9, "worst_year95": -0.05},
         "assets": [],
         "warnings": ["目标 10% 不可行：已按最大可行收益 9% 交付（低于目标 10%）。", "测试标的 DEMO1 已跳过: 无数据"]}
        """
        let result = try decode(json)
        XCTAssertNil(result.warningItems)
        XCTAssertEqual(result.classifiedWarnings.map(\.severity), [.critical, .warning])
        XCTAssertTrue(result.hasCriticalWarnings)
    }

    func testDecodeNewJSONWithWarningItems() throws {
        let json = """
        {"portfolio": {"expected_return": 0.09, "volatility": 0.10, "sharpe": 0.9, "worst_year95": -0.05},
         "assets": [],
         "warnings": ["目标 10% 不可行：已按最大可行收益 9% 交付（低于目标 10%）。"],
         "warning_items": [{"code": "target.infeasible", "message": "目标 10% 不可行：已按最大可行收益 9% 交付（低于目标 10%）。"},
                           {"code": "sleeve.levels_lifted", "message": "组合层复核抬升了袖内水平。"}]}
        """
        let result = try decode(json)
        XCTAssertEqual(result.warningItems?.count, 2)
        XCTAssertEqual(result.classifiedWarnings.map(\.kind), [.targetInfeasible, .sleeveLevelsLifted])
        XCTAssertEqual(result.classifiedWarnings.map(\.severity), [.critical, .info])
        XCTAssertTrue(result.hasCriticalWarnings)
    }

    func testDecodeWithoutAnyWarningFieldsIsStillValid() throws {
        let result = try decode(baseJSON)
        XCTAssertNil(result.warnings)
        XCTAssertNil(result.warningItems)
        XCTAssertTrue(result.classifiedWarnings.isEmpty)
    }
}
