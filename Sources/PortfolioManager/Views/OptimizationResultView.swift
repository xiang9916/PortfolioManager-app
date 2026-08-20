import SwiftUI
import Charts
import PortfolioCore

/// 能力2：优化结果展示（权重 + 收益/波动/夏普 + 基准对比）。
public struct OptimizationResultView: View {
    let result: OptimizationResult
    @Environment(\.dismiss) private var dismiss

    public var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text("优化结果").font(.title2).fontWeight(.semibold)
                Spacer()
                Button("关闭") { dismiss() }.keyboardShortcut(.cancelAction)
            }

            // Summary
            HStack(spacing: 12) {
                statCard("预期收益", pct(result.portfolio.expectedReturn))
                statCard("波动率", pct(result.portfolio.volatility))
                statCard("夏普比率", String(format: "%.3f", result.portfolio.sharpe))
                statCard("95%最差年度", pct(result.portfolio.worstYear95))
            }

            // Weights bar chart
            Text("资产权重").font(.headline)
            Chart(result.assets.sorted(by: { $0.weight > $1.weight }), id: \.key) { a in
                BarMark(
                    x: .value("权重", a.weight),
                    y: .value("标的", a.name)
                )
                .foregroundStyle(.blue.gradient)
                .annotation(position: .trailing) {
                    Text(pct(a.weight)).font(.caption2).monospacedDigit()
                }
            }
            .frame(height: max(220, CGFloat(result.assets.count) * 28))

            if let b = result.benchmark {
                Divider()
                Text("基准对比（沪深300 + 标普500）").font(.headline)
                HStack(spacing: 12) {
                    statCard("组合 Alpha", pct(b.portfolioAlpha))
                    statCard("组合 Beta", String(format: "%.2f", b.portfolioBeta))
                    statCard("波动降低", pct(b.volatilityReduction))
                    statCard("最差年度改善", pct(b.worstYearImprovement))
                }
            }
        }
        .padding()
        .frame(minWidth: 640, minHeight: 460)
    }

    private func statCard(_ t: String, _ v: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(t).font(.caption).foregroundStyle(.secondary)
            Text(v).font(.title3).fontWeight(.semibold).monospacedDigit()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 10))
    }
    private func pct(_ v: Double) -> String { String(format: "%.2f%%", v * 100) }
}
