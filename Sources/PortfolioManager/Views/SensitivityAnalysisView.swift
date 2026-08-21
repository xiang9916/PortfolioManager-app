import SwiftUI
import Charts
import PortfolioCore

/// 敏感性分析弹窗：展示目标收益率从 5% 步进到最大可行收益率的结果。
public struct SensitivityAnalysisView: View {
    let result: SensitivityAnalysisResult
    @Environment(\.dismiss) private var dismiss

    public var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                MacCloseButton { dismiss() }
                Text("敏感性分析").font(.title2).fontWeight(.semibold)
                Spacer()
            }

            // Summary
            HStack(spacing: 12) {
                statCard("最大可行收益率", pct(result.maxFeasibleReturn))
                statCard("无风险利率", pct(result.rf))
                statCard("境内池", pct(result.domesticWeight))
                statCard("境外池", pct(result.overseasWeight))
            }

            // Row 1: Volatility + Sharpe side by side (heights shrunk 20%)
            HStack(spacing: 16) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("波动率 vs 目标收益率").font(.headline)
                    Chart(feasiblePoints, id: \.targetReturn) { (p: SensitivityPoint) in
                        BarMark(
                            x: .value("目标收益率", p.targetReturn * 100),
                            y: .value("波动率", (p.volatility ?? 0) * 100)
                        )
                        .foregroundStyle(.orange.gradient)
                    }
                    .chartXScale(domain: minTargetPct...maxTargetPct)
                    .chartYAxis { AxisMarks(position: .leading) }
                    .frame(height: 144)
                }
                VStack(alignment: .leading, spacing: 4) {
                    Text("夏普比率 vs 目标收益率").font(.headline)
                    Chart(feasiblePoints, id: \.targetReturn) { (p: SensitivityPoint) in
                        LineMark(
                            x: .value("目标收益率", p.targetReturn * 100),
                            y: .value("夏普比率", p.sharpe ?? 0)
                        )
                        .foregroundStyle(.blue)
                        .interpolationMethod(.catmullRom)
                        PointMark(
                            x: .value("目标收益率", p.targetReturn * 100),
                            y: .value("夏普比率", p.sharpe ?? 0)
                        )
                        .foregroundStyle(.blue)
                    }
                    .chartXScale(domain: minTargetPct...maxTargetPct)
                    .chartYAxis { AxisMarks(position: .leading) }
                    .frame(height: 144)
                }
            }

            // Row 2: Long-term CAGR vs Target Return — two single-series charts
            // side by side (left: fat-tail hybrid, right: lognormal). One series
            // per Chart sidesteps the Swift Charts multi-LineMark merge bug.
            VStack(alignment: .leading, spacing: 4) {
                Text("长期复利CAGR vs 目标收益率").font(.headline)
                HStack(alignment: .top, spacing: 16) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("CAGR 肥尾混合（左尾 t\(tailDofLabel) + 右侧对数正态）")
                            .font(.caption).foregroundStyle(.red)
                        Chart(feasiblePoints, id: \.targetReturn) { (p: SensitivityPoint) in
                            LineMark(
                                x: .value("目标收益率", p.targetReturn * 100),
                                y: .value("CAGR肥尾", cagrFatTail(p) * 100)
                            )
                            .foregroundStyle(.red)
                            .interpolationMethod(.catmullRom)
                            PointMark(
                                x: .value("目标收益率", p.targetReturn * 100),
                                y: .value("CAGR肥尾", cagrFatTail(p) * 100)
                            )
                            .foregroundStyle(.red)
                        }
                        .chartXScale(domain: minTargetPct...maxTargetPct)
                        .chartYAxis { AxisMarks(position: .leading) }
                        .frame(height: 160)
                    }
                    VStack(alignment: .leading, spacing: 4) {
                        Text("CAGR 对数正态（精确）")
                            .font(.caption).foregroundStyle(.green)
                        Chart(feasiblePoints, id: \.targetReturn) { (p: SensitivityPoint) in
                            LineMark(
                                x: .value("目标收益率", p.targetReturn * 100),
                                y: .value("CAGR对数正态", cagrLognormal(p) * 100)
                            )
                            .foregroundStyle(.green)
                            .interpolationMethod(.catmullRom)
                            PointMark(
                                x: .value("目标收益率", p.targetReturn * 100),
                                y: .value("CAGR对数正态", cagrLognormal(p) * 100)
                            )
                            .foregroundStyle(.green)
                        }
                        .chartXScale(domain: minTargetPct...maxTargetPct)
                        .chartYAxis { AxisMarks(position: .leading) }
                        .frame(height: 160)
                    }
                }
                Text("CAGR = exp(E[ln(1+R)])−1。肥尾混合：亏损侧 R≤0 ~ t\(tailDofLabel)（尾部指数 α=\(tailDofLabel)，方差匹配，单年损失下限 −99.9%），亏损概率与盈利分布同对数正态，拼接点 R=0")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }

            // Detail table
            Text("详细数据").font(.headline)
            ScrollView {
                VStack(spacing: 0) {
                    HStack(spacing: 0) {
                        tableHeader("目标")
                        tableHeader("收益")
                        tableHeader("波动")
                        tableHeader("夏普")
                        tableHeader("最差年")
                        tableHeader("CAGR正态")
                        tableHeader("CAGR肥尾混合")
                    }
                    .padding(.vertical, 3)
                    Divider()
                    ForEach(result.points, id: \.id) { p in
                        HStack(spacing: 0) {
                            Text(p.feasible ? pctShort(p.targetReturn) : "\(pctShort(p.targetReturn)) ✗")
                                .frame(maxWidth: .infinity)
                                .foregroundStyle(p.feasible ? Color.primary : Color.red)
                            Text(p.feasible && p.achievedReturn != nil ? pctShort(p.achievedReturn!) : "—")
                                .frame(maxWidth: .infinity)
                                .foregroundStyle(.secondary)
                            Text(p.feasible && p.volatility != nil ? pctShort(p.volatility!) : "—")
                                .frame(maxWidth: .infinity)
                                .foregroundStyle(.secondary)
                            Text(p.feasible && p.sharpe != nil ? String(format: "%.3f", p.sharpe!) : "—")
                                .frame(maxWidth: .infinity)
                                .foregroundStyle(.secondary)
                            Text(p.feasible && p.worstYear95 != nil ? pctShort(p.worstYear95!) : "—")
                                .frame(maxWidth: .infinity)
                                .foregroundStyle(p.feasible && p.worstYear95 != nil && p.worstYear95! < -0.15 ? Color.red : Color.secondary)
                            Text(p.feasible && p.volatility != nil ? pctShort(cagrLognormal(p)) : "—")
                                .frame(maxWidth: .infinity)
                                .foregroundStyle(Color.green)
                            Text(p.feasible && p.volatility != nil ? pctShort(cagrFatTail(p)) : "—")
                                .frame(maxWidth: .infinity)
                                .foregroundStyle(Color.red)
                        }
                        .font(.caption2)
                        .monospacedDigit()
                        .padding(.vertical, 2)
                        Divider().opacity(0.3)
                    }
                }
            }
            .frame(maxHeight: 180)
        }
        .padding()
        .frame(minWidth: 720, minHeight: 640)
    }

    // MARK: - Helpers

    private var feasiblePoints: [SensitivityPoint] {
        result.points.filter { $0.feasible }
    }

    /// X-axis domain: lower bound = first scanned target (= --step-min),
    /// upper = max feasible target + 0.5 margin. 取全部点（含不可行）的
    /// 最小值而非可行点最小值——首个扫描点恒为 step-min，即使其不可行。
    private var minTargetPct: Double {
        result.points.map { $0.targetReturn * 100 }.min() ?? 5
    }

    /// Max target return in percentage, for x-axis domain.
    private var maxTargetPct: Double {
        let mx = feasiblePoints.map { $0.targetReturn * 100 }.max() ?? 14
        return max(mx + 0.5, minTargetPct + 1)
    }

    /// 对数正态精确 CAGR：E[ln(1+R)] = ln(1+μ) − ln(1+σ²/(1+μ)²)/2（旧数据缺字段时本地回退）
    private func cagrLognormal(_ p: SensitivityPoint) -> Double {
        if let v = p.cagrLognormal { return v }
        guard let ret = p.achievedReturn, let vol = p.volatility else { return 0 }
        let m = 1 + ret
        guard vol > 0 else { return ret }
        let s2 = log(1 + vol * vol / (m * m))
        return exp(log(m) - s2 / 2) - 1
    }

    /// 肥尾 CAGR：Python 端 Student-t(α) 数值积分结果；旧数据缺字段时退回旧近似 μ−σ²/2
    private func cagrFatTail(_ p: SensitivityPoint) -> Double {
        if let v = p.cagrFatTail { return v }
        guard let ret = p.achievedReturn, let vol = p.volatility else { return 0 }
        return ret - vol * vol / 2
    }

    /// 肥尾模型左尾自由度标签（缺省 3）
    private var tailDofLabel: String {
        guard let v = result.tailDof else { return "3" }
        return v == v.rounded() ? String(format: "%.0f", v) : String(format: "%.1f", v)
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

    private func tableHeader(_ t: String) -> some View {
        Text(t).frame(maxWidth: .infinity).font(.caption2).fontWeight(.semibold)
    }

    private func pct(_ v: Double) -> String { String(format: "%.2f%%", v * 100) }
    private func pctShort(_ v: Double) -> String { String(format: "%.1f%%", v * 100) }
}
