import XCTest
@testable import PortfolioCore

/// 结果模型「持仓纳入 + 敏感性锚定」的加法式字段解码测试。
///
/// 覆盖：`sleeve_params` / `held_assets` / `assets[].is_held` /
/// `points[].fallback` / `anchor`，以及**旧结果文件缺这些键 → nil** 的兼容性
/// （旧 JSON 必须能解码，新字段全部 optional）。
///
/// 所有 fixture 都是虚构的代码 / 类别名 / 比率，不含任何真实持仓、金额或标的。
final class OptimizationResultModelsTests: XCTestCase {

    private func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }

    private func decodeResult(_ json: String) throws -> OptimizationResult {
        try decoder().decode(OptimizationResult.self, from: Data(json.utf8))
    }

    private func decodeSensitivity(_ json: String) throws -> SensitivityAnalysisResult {
        try decoder().decode(SensitivityAnalysisResult.self, from: Data(json.utf8))
    }

    /// 旧格式 summary：没有 sleeve_params / held_assets / is_held（本轮新增的键一个都没有）。
    private let legacyResultJSON = """
    {"portfolio": {"expected_return": 0.075, "volatility": 0.11,
                   "sharpe": 0.6818, "worst_year_95": -0.1406},
     "assets": [{"key": "DEMO_A", "name": "[DEMO] 虚构资产 A", "weight": 0.6,
                 "expected_return": 0.08, "volatility": 0.12, "sharpe": 0.6667}],
     "target_return": 0.07, "target_gap": 0.005}
    """

    // MARK: - 新字段解码

    func testDecodeSleeveParamsHeldAssetsAndIsHeld() throws {
        let json = """
        {"portfolio": {"expected_return": 0.08, "volatility": 0.12,
                       "sharpe": 0.6667, "worst_year_95": -0.15},
         "assets": [
            {"key": "DEMO_A", "name": "[DEMO] 虚构资产 A", "weight": 0.55,
             "expected_return": 0.08, "volatility": 0.12, "sharpe": 0.6667},
            {"key": "H_DEMO9", "name": "[持仓] DEMO9（[持仓] what-if，不进执行清单）", "weight": 0.05,
             "expected_return": 0.06, "volatility": 0.20, "sharpe": 0.30, "is_held": true}
         ],
         "sleeve_params": {"SLEEVE_A": [0.08, 0.12], "SLEEVE_B": [0.06, 0.09]},
         "held_assets": {
            "included": [{"ticker": "DEMO9", "mu": 0.06, "vol": 0.2, "n_days": 900,
                          "pool": "overseas", "test_class": "active", "window_years": 10,
                          "mu_note": "history_only", "vol_note": "history", "is_held": true}],
            "skipped": [{"ticker": "DEMO8", "reason": "行情历史不足 30 天"}]
         }}
        """
        let result = try decodeResult(json)

        // sleeve_params: [String: [Double]]
        XCTAssertEqual(result.sleeveParams?.count, 2)
        XCTAssertEqual(result.sleeveParams?["SLEEVE_A"], [0.08, 0.12])
        XCTAssertEqual(result.sleeveParams?["SLEEVE_B"], [0.06, 0.09])

        // assets[].is_held：普通行缺键 → nil；持仓注入行 → true（旧结果兼容的关键）。
        XCTAssertEqual(result.assets.count, 2)
        XCTAssertNil(result.assets[0].isHeld)
        XCTAssertEqual(result.assets[1].isHeld, true)

        // held_assets.included：逐字段解码（n_days / test_class / window_years / *_note）。
        let included = try XCTUnwrap(result.heldAssets?.included)
        XCTAssertEqual(included.count, 1)
        let e = try XCTUnwrap(included.first)
        XCTAssertEqual(e.ticker, "DEMO9")
        XCTAssertNil(e.name)             // Python 侧可省略
        XCTAssertEqual(e.mu, 0.06)
        XCTAssertEqual(e.vol, 0.2)
        XCTAssertEqual(e.nDays, 900)
        XCTAssertEqual(e.pool, "overseas")
        XCTAssertEqual(e.testClass, "active")
        XCTAssertEqual(e.windowYears, 10)
        XCTAssertEqual(e.muNote, "history_only")
        XCTAssertEqual(e.volNote, "history")
        XCTAssertEqual(e.isHeld, true)

        // held_assets.skipped：ticker + 原因。
        let skipped = try XCTUnwrap(result.heldAssets?.skipped)
        XCTAssertEqual(skipped.map(\.ticker), ["DEMO8"])
        XCTAssertEqual(skipped.first?.reason, "行情历史不足 30 天")
    }

    func testDecodeHeldAssetsToleratesMissingFields() throws {
        // 加法式输出：included 项可能只带 ticker（甚至空对象），缺字段一律 nil、不得整条解码失败。
        let json = """
        {"portfolio": {"expected_return": 0.075, "volatility": 0.11,
                       "sharpe": 0.6818, "worst_year_95": -0.1406},
         "assets": [],
         "held_assets": {"included": [{"ticker": "DEMO9"}, {}], "skipped": []}}
        """
        let result = try decodeResult(json)
        let included = try XCTUnwrap(result.heldAssets?.included)
        XCTAssertEqual(included.count, 2)
        XCTAssertEqual(included[0].ticker, "DEMO9")
        XCTAssertNil(included[0].mu)
        XCTAssertNil(included[0].nDays)
        XCTAssertNil(included[1].ticker)
        XCTAssertEqual(result.heldAssets?.skipped?.isEmpty, true)
    }

    // MARK: - 旧结果文件兼容（缺键 → nil）

    func testDecodeLegacyResultWithoutNewKeys() throws {
        let result = try decodeResult(legacyResultJSON)
        XCTAssertEqual(result.portfolio.expectedReturn, 0.075)
        XCTAssertNil(result.sleeveParams)
        XCTAssertNil(result.heldAssets)
        XCTAssertNil(result.assets.first?.isHeld)
    }

    // MARK: - 敏感性：points[].fallback + anchor

    func testDecodeSensitivityFallbackFlag() throws {
        let json = """
        {"max_feasible_return": 0.095, "rf": 0.02,
         "domestic_weight": 0.45, "overseas_weight": 0.55,
         "points": [
            {"target_return": 0.05, "feasible": true,
             "achieved_return": 0.08, "volatility": 0.10, "sharpe": 0.60},
            {"target_return": 0.09, "feasible": true,
             "achieved_return": 0.085, "volatility": 0.13, "sharpe": 0.50, "fallback": true},
            {"target_return": 0.12, "feasible": false}
         ]}
        """
        let result = try decodeSensitivity(json)
        XCTAssertEqual(result.points.count, 3)
        XCTAssertNil(result.points[0].fallback)          // 旧点/正常点没有该键
        XCTAssertEqual(result.points[1].fallback, true)  // 回退点
        XCTAssertTrue(result.points[2].fallback == nil)  // 不可行点不依赖该键
        XCTAssertNil(result.anchor)
    }

    func testDecodeSensitivityAnchorBlock() throws {
        let json = """
        {"max_feasible_return": 0.095, "rf": 0.02,
         "domestic_weight": 0.45, "overseas_weight": 0.55,
         "points": [{"target_return": 0.07, "feasible": true,
                     "achieved_return": 0.075, "volatility": 0.11, "sharpe": 0.6818}],
         "anchor": {"target": 0.07, "matched": true,
                    "point": {"ret": 0.075, "vol": 0.11, "sharpe": 0.6818},
                    "main": {"ret": 0.075, "vol": 0.11, "sharpe": 0.6818}}}
        """
        let result = try decodeSensitivity(json)
        let anchor = try XCTUnwrap(result.anchor)
        XCTAssertEqual(anchor.target, 0.07)
        XCTAssertEqual(anchor.matched, true)
        XCTAssertEqual(anchor.point?.ret, 0.075)
        XCTAssertEqual(anchor.point?.sharpe, 0.6818)
        XCTAssertEqual(anchor.main?.vol, 0.11)
        XCTAssertNil(result.points.first?.fallback)
    }

    func testDecodeLegacySensitivityWithoutNewKeys() throws {
        let json = """
        {"max_feasible_return": 0.095, "rf": 0.02,
         "domestic_weight": 0.45, "overseas_weight": 0.55,
         "points": [{"target_return": 0.05, "feasible": true,
                     "achieved_return": 0.08, "volatility": 0.10, "sharpe": 0.60}]}
        """
        let result = try decodeSensitivity(json)
        XCTAssertNil(result.points.first?.fallback)
        XCTAssertNil(result.points.first?.sourceTarget)
        XCTAssertNil(result.points.first?.repaired)
        XCTAssertNil(result.anchor)
    }

    // MARK: - 敏感性：候选包络 source_target / repaired

    func testDecodeSensitivityEnvelopeFields() throws {
        let json = """
        {"max_feasible_return": 0.105, "rf": 0.02,
         "domestic_weight": 0.45, "overseas_weight": 0.55,
         "points": [
            {"target_return": 0.09, "feasible": true,
             "achieved_return": 0.10, "volatility": 0.0576, "sharpe": 1.39,
             "source_target": 0.10},
            {"target_return": 0.095, "feasible": true,
             "achieved_return": 0.095, "volatility": 0.0517, "sharpe": 1.45,
             "repaired": true},
            {"target_return": 0.10, "feasible": true,
             "achieved_return": 0.10, "volatility": 0.0576, "sharpe": 1.39}
         ]}
        """
        let result = try decodeSensitivity(json)
        XCTAssertEqual(result.points.count, 3)
        // 借用更高目标的候选：source_target 记录来源、repaired 缺失
        XCTAssertEqual(result.points[0].sourceTarget, 0.10)
        XCTAssertNil(result.points[0].repaired)
        // 支配修复：本点自己的解被替换
        XCTAssertNil(result.points[1].sourceTarget)
        XCTAssertEqual(result.points[1].repaired, true)
        // 正常点：两个键都没有
        XCTAssertNil(result.points[2].sourceTarget)
        XCTAssertNil(result.points[2].repaired)
    }
}
