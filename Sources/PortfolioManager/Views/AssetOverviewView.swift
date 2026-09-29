import SwiftUI
import Charts
import PortfolioCore

/// 模块1：资产管理 — 当前资产大类配置 + 历史财务表现 + 可视化图表。
public struct AssetOverviewView: View {
    @Bindable var store: AppStore

    public var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                // Summary cards
                if let alloc = store.allocation {
                    summaryCards(alloc)
                }
                // Allocation donut + legend
                if let alloc = store.allocation, !alloc.slices.isEmpty {
                    allocationSection(alloc)
                }
                // Historical performance line chart
                if !store.performancePoints.isEmpty {
                    performanceSection()
                }
            }
            .padding()
        }
        .navigationTitle("资产管理")
        // 右上角只有全局按钮（更新行情 / 隐藏数字 / 导出备份 / 导入备份）。
        .toolbar {
            GlobalToolbarContent(store: store)
        }
        .overlay(alignment: .top) {
            if let msg = store.statusMessage {
                Text(msg)
                    .font(.caption)
                    .padding(.horizontal, 12).padding(.vertical, 6)
                    .background(.ultraThinMaterial, in: Capsule())
                    .padding(.top, 8)
            }
        }
    }

    // MARK: summary cards

    private func summaryCards(_ alloc: AllocationSnapshot) -> some View {
        let s = store.performanceSummary
        let div = store.dividendSummary
        let dividendTotal = div?.totalNetCny ?? 0
        // 全资产股息率 = 全资产股息 ÷ 总资产 (分母与顶部「总资产」同值, 含跨池 → 分子分母同口径).
        let dividendYield = alloc.totalValue > 0 ? dividendTotal / alloc.totalValue : 0
        let coverage = (div?.coveredCount ?? 0).description + "/" + (div?.assetCount ?? store.perspectives.count).description
        let dividendHelp = """
        全资产股息 = Σ 各资产「今年预计股息（折人民币，税后）」。
        口径：按除息日归入自然年；今年预计每股股息 = max(0, 2×去年每股股息 − 前年每股股息)；
        再 × 份额 × 汇率 × (1 − 股息税率)。美股 10% / 港股 28% / A股 0%。
        数据源 Yahoo + 天天基金，逐年窗口抓取。
        覆盖 \(coverage) 个资产（「—」= 数据源无覆盖或抓取失败，按 0 计入）。
        更新：\(div?.fetchedAt ?? "尚未抓取")
        """
        let yieldHelp = """
        全资产股息率 = 全资产股息 ÷ 总资产。
        分母与顶部「总资产」一致（含跨池），分子分母同口径。
        覆盖 \(coverage) 个资产；未覆盖的资产按 0 计入，会低估该比率。
        """
        return VStack(alignment: .leading, spacing: 16) {
            // 4 列 2 行: 总资产/境内/境外/全资产股息 · 近三年收益/年化波动/最大回撤/全资产股息率
            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 16), count: 4),
                      spacing: 16) {
                metricCard("总资产", money(alloc.totalValue), "chart.pie.fill", .blue)
                metricCard("境内", money(alloc.domesticValue), "house.fill", .teal)
                metricCard("境外", money(alloc.overseasValue), "globe", .indigo)
                metricCard("全资产股息", money2(dividendTotal), "banknote", .mint, help: dividendHelp)
                metricCard("近3年收益", perf(s?.totalReturn), "chart.line.uptrend.xyaxis",
                           (s?.totalReturn ?? 0) >= 0 ? .green : .red)
                metricCard("年化波动", perf(s?.annualizedVolatility), "waveform.path.ecg", .orange)
                metricCard("最大回撤", perf(s?.maxDrawdown), "arrow.down.right", .purple)
                metricCard("全资产股息率", pct(dividendYield), "percent", .pink, help: yieldHelp)
            }
            // 「跨池」不在 4×2 主网格里: 跨池是资产大类层面的属性, 标的级 pool=cross 才有金额。
            // 条件性附加行 — 不占你指定的 8 个位, 也不静默丢信息 (当前数据 cross=0, 不显示)。
            if alloc.crossValue > 0 {
                metricCard("跨池", money(alloc.crossValue), "arrow.triangle.swap", .yellow,
                           help: "标的级 pool=跨池 的持仓市值合计（相当于现金，不计入境内/境外）。")
            }
        }
    }

    /// 绩效卡缺数据时显示「—」, 保持 4×2 网格形状稳定.
    private func perf(_ v: Double?) -> String {
        guard let v else { return "—" }
        return pct(v)
    }

    @ViewBuilder
    private func metricCard(_ title: String, _ value: String, _ icon: String, _ color: Color,
                            help: String? = nil) -> some View {
        let card = VStack(alignment: .leading, spacing: 6) {
            Label(title, systemImage: icon).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.title2).fontWeight(.semibold).monospacedDigit()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
        .background(color.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
        if let help {
            card.help(Text(help))
        } else {
            card
        }
    }

    // MARK: allocation

    private func allocationSection(_ alloc: AllocationSnapshot) -> some View {
        HStack(alignment: .top, spacing: 24) {
            // Donut
            if store.hideNumbers {
                PrivacyPlaceholder().frame(width: 260, height: 260)
            } else {
                Chart(alloc.slices) { slice in
                    SectorMark(
                        angle: .value("占比", slice.weight),
                        innerRadius: .ratio(0.62),
                        angularInset: 1.5
                    )
                    .cornerRadius(3)
                    .foregroundStyle(AssetClassStyle.color(slice.assetClass))
                }
                .frame(width: 260, height: 260)
            }

            // Legend + values
            VStack(alignment: .leading, spacing: 8) {
                Text("资产大类配置").font(.headline)
                ForEach(alloc.slices) { slice in
                    HStack(spacing: 8) {
                        Circle().fill(AssetClassStyle.color(slice.assetClass)).frame(width: 10, height: 10)
                        Text(AssetClassStyle.displayName(slice.assetClass)).font(.subheadline)
                        Spacer()
                        Text(money(slice.value)).font(.subheadline).monospacedDigit()
                        Text(pct(slice.weight)).font(.subheadline).foregroundStyle(.secondary).monospacedDigit()
                            .frame(width: 64, alignment: .trailing)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding()
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 12))
    }

    // MARK: performance

    private func performanceSection() -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("模拟历史财务表现（加权净值，近3年）").font(.headline)
                .help("口径：以当前持仓市值权重，将各标的累计净值在近3年窗口内归一为1.0后加权合成；不含汇率、不含历史调仓。基金用累计净值（含分红再投），股票用收盘价。基准=沪深300+标普500按当前境内/境外池占比加权（与优化器一致）。")
            if store.hideNumbers {
                PrivacyPlaceholder().frame(height: 260)
            } else {
                performanceChart
            }
        }
        .padding()
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 12))
    }

    private var performanceChart: some View {
        let pts = store.performancePoints.chartPoints(series: "组合")
        let benchPts = store.benchmarkPoints.chartPoints(series: "基准")
        // 显式 y 域: 避免自动域撑到 0..3 导致顶部浮出无意义网格线.
        let allValues = (store.performancePoints + store.benchmarkPoints).map { $0.value }
        let lo = (allValues.min() ?? 1.0) - 0.03
        let hi = (allValues.max() ?? 1.0) + 0.03
        // 多系列必须用 foregroundStyle(by:) + chartForegroundStyleScale 区分系列:
        // 直接给第二个 ForEach 的 LineMark 设静态颜色会被 Charts 忽略,
        // 基准线会回落到默认调色板第一色(蓝) —— 勿改回静态前景色写法.
        return Chart {
            ForEach(pts) { p in
                LineMark(x: .value("日期", p.date), y: .value("净值", p.value))
                    .foregroundStyle(by: .value("系列", "组合"))
                    .lineStyle(StrokeStyle(lineWidth: 1.8))
            }
            ForEach(benchPts) { p in
                LineMark(x: .value("日期", p.date), y: .value("净值", p.value))
                    .foregroundStyle(by: .value("系列", "基准"))
                    .lineStyle(StrokeStyle(lineWidth: 1.5, dash: [4, 3]))
            }
        }
        .chartForegroundStyleScale(["组合": .blue, "基准": .green])
        .chartYScale(domain: lo...hi)
        .chartYAxis {
            // 只留刻度文字, 不画水平网格线 —— 悬在数据上方的横线曾被误认为"多余的直线".
            AxisMarks { _ in
                AxisValueLabel()
            }
        }
        .chartLegend(.hidden)
        .overlay(alignment: .topTrailing) {
            // 右上角图例: 自绘 overlay (chartLegend 自定义内容不渲染, 勿改回).
            HStack(spacing: 12) {
                HStack(spacing: 4) {
                    Rectangle().fill(.blue).frame(width: 12, height: 3)
                    Text("回测数据（组合）").font(.caption).foregroundStyle(.secondary)
                }
                HStack(spacing: 4) {
                    Rectangle().fill(.green).frame(width: 12, height: 3)
                    Text("基准数据（沪深300+标普500）").font(.caption).foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal, 8).padding(.vertical, 4)
            .background(.background.opacity(0.72), in: RoundedRectangle(cornerRadius: 6))
            .padding(6)
        }
        .frame(height: 260)
    }

    // MARK: helpers

    private func money(_ v: Double) -> String {
        if store.hideNumbers { return PrivacyStyle.masked }
        let f = NumberFormatter()
        f.numberStyle = .decimal
        f.maximumFractionDigits = 0
        return "¥" + (f.string(from: NSNumber(value: v)) ?? "0")
    }
    /// 股息金额用两位小数 (金额小, 取整会看不出差别).
    private func money2(_ v: Double) -> String {
        if store.hideNumbers { return PrivacyStyle.masked }
        let f = NumberFormatter()
        f.numberStyle = .decimal
        f.minimumFractionDigits = 2
        f.maximumFractionDigits = 2
        return "¥" + (f.string(from: NSNumber(value: v)) ?? "0.00")
    }
    private func pct(_ v: Double) -> String {
        if store.hideNumbers { return PrivacyStyle.masked }
        return String(format: "%.2f%%", v * 100)
    }
}
