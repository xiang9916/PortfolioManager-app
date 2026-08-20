import Foundation

/// Decodes the optimizer's portfolio_result.json into typed models.
public enum ResultImporter {
    public static func decode(url: URL) throws -> OptimizationResult {
        let data = try Data(contentsOf: url)
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(OptimizationResult.self, from: data)
    }

    /// Human-readable summary for logs / CLI / AI consumption.
    public static func summarize(_ r: OptimizationResult) -> String {
        var lines: [String] = []
        lines.append("投资组合优化结果")
        lines.append(String(format: "  预期收益 %.2f%%  波动 %.2f%%  夏普 %.3f  95%%最差年度 %.2f%%",
                            r.portfolio.expectedReturn * 100,
                            r.portfolio.volatility * 100,
                            r.portfolio.sharpe,
                            r.portfolio.worstYear95 * 100))
        if let b = r.benchmark {
            lines.append(String(format: "  基准: 收益 %.2f%% 夏普 %.3f | alpha %.2f%%  beta %.3f 波动改善 %.2f%%",
                                b.expectedReturn * 100, b.sharpe,
                                b.portfolioAlpha * 100, b.portfolioBeta,
                                b.volatilityReduction * 100))
        }
        lines.append("  配置明细:")
        for a in r.assets.sorted(by: { $0.weight > $1.weight }) {
            lines.append(String(format: "    %-28@ %6.2f%%  收益 %.2f%%  夏普 %.3f",
                                a.name, a.weight * 100, a.expectedReturn * 100, a.sharpe))
        }
        return lines.joined(separator: "\n")
    }
}
