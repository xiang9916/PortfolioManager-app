import SwiftUI
import Charts
import PortfolioCore

/// 敏感性分析弹窗：展示目标收益率从 5% 步进到最大可行收益率的结果。
public struct SensitivityAnalysisView: View {
    let result: SensitivityAnalysisResult
    /// 主结果的实际落点（目标收益率 / 夏普 / 波动），用来在图上标一个「当前交付」参考点 ——
    /// 图与结果页必须同源，这个点应当正好落在曲线上（2026-10-14 起）。
    var currentTarget: Double? = nil
    var currentSharpe: Double? = nil
    var currentVol: Double? = nil
    @Environment(\.dismiss) private var dismiss

    public var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                MacCloseButton { dismiss() }
                Text("敏感性分析").font(.title2).fontWeight(.semibold)
                Spacer()
            }
            if let scope = result.scope {
                Text("口径：\(scope)。每一点都是「把目标设成它、实际会交付的组合」；蓝点 = 同一锚定口径下找到的最优可交付组合；绿点 = 主结果锚点。")
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                // 旧输出没有 scope：图注仍然要说明蓝点/绿点的口径。
                Text("蓝点 = 同一锚定口径下找到的最优可交付组合；绿点 = 主结果锚点。")
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
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
                    // 蓝线/蓝点只画「可交付」点：feasible 且未回退（fallback = 该目标下交付低于目标，
                    // 是回退结果而非该目标下的最优交付组合）。回退点仍保留在下方明细表里（标「回退」）。
                    Chart {
                        ForEach(deliverablePoints, id: \.targetReturn) { (p: SensitivityPoint) in
                            LineMark(
                                x: .value("目标收益率", p.targetReturn * 100),
                                y: .value("夏普比率", p.sharpe ?? 0)
                            )
                            .foregroundStyle(.blue)
                            .interpolationMethod(.monotone)
                            PointMark(
                                x: .value("目标收益率", p.targetReturn * 100),
                                y: .value("夏普比率", p.sharpe ?? 0)
                            )
                            .foregroundStyle(.blue)
                        }
                        if let t = currentTarget, let s = currentSharpe {
                            PointMark(x: .value("目标收益率", t * 100), y: .value("夏普比率", s))
                                .foregroundStyle(.green)
                                .symbolSize(90)
                                .annotation(position: .top) {
                                    Text("主结果锚点").font(.caption2).foregroundStyle(.green)
                                }
                        }
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
                            // 「目标」列：不可行加 ✗；回退点显式标「回退」（交付低于该点目标，
                            // 不是该目标下能交付的组合），不再伪装成正常点。
                            Text(targetCellText(p))
                                .frame(maxWidth: .infinity)
                                .foregroundStyle(targetCellColor(p))
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

    /// 蓝色前沿上的点：feasible 且**未回退**（fallback）。
    /// 回退点（目标不可行 → 交付的是最大可行收益）不画进曲线，否则「目标 T 处的最优交付」
    /// 会被一条低于主结果的假前沿覆盖；它们仍在表格里并标「回退」。
    private var deliverablePoints: [SensitivityPoint] {
        result.points.filter { $0.feasible && !($0.fallback ?? false) }
    }

    /// 「目标」列的文本：不可行加 ✗，回退点加「回退」。
    private func targetCellText(_ p: SensitivityPoint) -> String {
        var text = pctShort(p.targetReturn)
        if !p.feasible { text += " ✗" }
        if p.fallback == true { text += " 回退" }
        return text
    }

    private func targetCellColor(_ p: SensitivityPoint) -> Color {
        if !p.feasible { return .red }
        return p.fallback == true ? .orange : .primary
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
