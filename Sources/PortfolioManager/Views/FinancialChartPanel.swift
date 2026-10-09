import SwiftUI
import Charts
import PortfolioCore

/// 财务分析 · 统计图面板 (能力4).
/// 单卡片 + 分段控件切换 5 张图, 数据全部来自 QuarterlyMetrics.compute 的派生列;
/// 视图层只做展示过滤 / 格式化, 不引入新的 Core 类型.
///
/// 设计原则: 一图一信息.
///   回报曲线  = 仅累计净总回报率 (Y 轴从 0, 纯色填充);
///   现金流    = 堆叠柱, 境内暖色 / 境外冷色, 类别内 股息→利息→资本利得 由深到浅;
///   投资流入  = 纯柱图 (新增投入), 柱顶直接标金额;
///   季度收益  = 滚动年化点 + 95% 置信区间误差棒 + 运行均值虚线;
///   本金与市值 = 本金打底填充 + 总市值线 (Y 轴从 0).
/// 约定: nil 数据 = 断线 / 不画柱 (不补 0); 金额自适应万/亿; 比率百分比; 绿涨红跌.
struct FinancialChartPanel: View {
    /// 按季末升序的派生列 (调用方已 compute).
    let columns: [QuarterComputed]
    /// 是否隐藏数字 (隐私): 为 true 时整块图表替换为 "***" 占位.
    let hideNumbers: Bool

    // MARK: - 状态

    @State private var tab: ChartTab = .returnCurve
    @State private var limitTo12 = false
    /// 当前悬停的季度标签 (quarterLabel).
    @State private var hoverLabel: String?

    enum ChartTab: String, CaseIterable, Identifiable {
        case returnCurve = "回报曲线"
        case cashFlow = "现金流"
        case investment = "投资流入"
        case quarterReturn = "季度收益"
        case capital = "本金与市值"
        var id: String { rawValue }
    }

    var body: some View {
        Group {
            if hideNumbers {
                PrivacyPlaceholder().frame(height: 280)
            } else {
                VStack(alignment: .leading, spacing: 10) {
                    header
                    if filtered.isEmpty {
                        emptyState
                    } else {
                        chartArea
                    }
                }
            }
        }
        .padding()
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 12))
        .onChange(of: tab) { _, _ in hoverLabel = nil }
    }

    // MARK: - 头部 (切换器 + 12 季过滤)

    private var header: some View {
        HStack(spacing: 12) {
            Picker("图表", selection: $tab) {
                ForEach(ChartTab.allCases) { t in
                    Text(t.rawValue).tag(t)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            Spacer()
            if columns.count > 12 {
                Toggle("仅最近 12 季", isOn: $limitTo12)
                    .toggleStyle(.switch)
                    .controlSize(.small)
                    .font(.caption)
                    .onChange(of: limitTo12) { _, _ in hoverLabel = nil }
            }
        }
    }

    /// 季度总数 ≤ 12 时过滤无意义, 恒为全量.
    private var filtered: [QuarterComputed] {
        if limitTo12 && columns.count > 12 {
            return Array(columns.suffix(12))
        }
        return columns
    }

    private var labels: [String] {
        filtered.map { QuarterlyMetrics.quarterLabel($0.report.periodEnd) }
    }

    private var emptyState: some View {
        VStack(spacing: 8) {
            Image(systemName: "chart.line.uptrend.xyaxis")
                .font(.system(size: 30, weight: .light))
                .foregroundStyle(.tertiary)
            Text("暂无季度财务数据")
                .font(.callout)
                .foregroundStyle(.secondary)
            Text("在下方逐季填写市值 / 成本 / 利息 / 股息后, 此处将展示趋势图。")
                .font(.caption)
                .foregroundStyle(.tertiary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 40)
    }

    // MARK: - 图表区

    private var chartArea: some View {
        VStack(alignment: .leading, spacing: 6) {
            Group {
                switch tab {
                case .returnCurve: returnChart
                case .cashFlow: cashChart
                case .investment: investChart
                case .quarterReturn: annualChart
                case .capital: capitalChart
                }
            }
            .frame(height: 280)
            .withHover(labels: labels, hovered: $hoverLabel, numeric: tab == .capital)

            if tab == .cashFlow {
                cashLegend
            }
            if tab == .capital {
                capitalLegend
            }
            if tab == .investment {
                investLegend
            }
            footer
        }
    }

    /// 底部信息行: 悬停时显示该季度读数, 否则显示当前图的说明.
    private var footer: some View {
        Group {
            if let label = hoverLabel {
                hoverSummary(label)
            } else {
                Text(tabDescription)
                    .foregroundStyle(.tertiary)
            }
        }
        .font(.caption)
        .frame(minHeight: 16, alignment: .leading)
    }

    private var tabDescription: String {
        switch tab {
        case .returnCurve:
            return "累计净总回报率 = (已实现现金流 + 未实现利得 − 税费) / 本金; 悬停查看精确值。"
        case .cashFlow:
            return "自下而上: 股息 (内→外) → 利息 (内→外) → 资本利得 (内→外); 暖色 = 境内, 冷色 = 境外, 同侧内由深到浅。悬停查看明细。"
        case .investment:
            return "堆叠柱: 一次投资 + 二次投资 = 当季新增投入 (柱顶标注合计); 悬停查看拆分与二次占比。"
        case .quarterReturn:
            return "点 = 滚动年化收益率; 棒 = 95% 置信区间 (均值±1.96σ), 棒越长 = 波动越大; 水平虚线 = 当前运行均值。年化 ≤ 0 表示当期亏损。"
        case .capital:
            return "自下而上: 累计股息 → 累计利息 → 累计已实现资本利得 → 本金 → 未实现资本利得；四个正层相加 = 总成本，最上面那条带 = 总市值与总成本之差（为正叠在顶部之上，为负压在顶部之内）。绿虚线 = 本金。悬停查看各层数值。"
        }
    }

    // MARK: - 图 1 · 回报曲线 (单线, 从 0 起, 渐变填充)

    private var returnChart: some View {
        let pts = pairPoints(labels, filtered.map { $0.cumNetReturnRate })
        let runs = splitRuns(pts)
        let maxV = max(pts.map(\.y).max() ?? 0, 0.01)
        return Chart {
            ForEach(Array(runs.enumerated()), id: \.offset) { _, run in
                ForEach(run, id: \.label) { p in
                    AreaMark(
                        x: .value("季度", p.label),
                        yStart: .value("基准", 0),
                        yEnd: .value("回报率", p.y)
                    )
                    .foregroundStyle(
                        LinearGradient(
                            colors: [Color.accentColor.opacity(0.22), Color.accentColor.opacity(0.02)],
                            startPoint: .top, endPoint: .bottom
                        )
                    )
                }
                ForEach(run, id: \.label) { p in
                    LineMark(
                        x: .value("季度", p.label),
                        y: .value("回报率", p.y)
                    )
                    .foregroundStyle(Color.accentColor)
                    .lineStyle(StrokeStyle(lineWidth: 2.5))
                    .symbol(.circle)
                    .symbolSize(24)
                }
            }
            if let last = pts.last {
                PointMark(x: .value("季度", last.label), y: .value("回报率", last.y))
                    .foregroundStyle(Color.accentColor)
                    .annotation(position: .top) {
                        Text(pct(last.y))
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.secondary)
                    }
            }
            hoverRule
        }
        .chartYScale(domain: 0...(maxV * 1.2))
        .chartYAxis {
            AxisMarks(position: .leading) { value in
                AxisGridLine()
                AxisValueLabel {
                    if let d = value.as(Double.self) {
                        Text("\(Int(d * 100))%")
                    }
                }
            }
        }
    }

    // MARK: - 图 2 · 现金流 (暖色境内 / 冷色境外, 类别内由深到浅)

    private struct CashSeries {
        let name: String
        let color: Color
        let value: (QuarterlyReport) -> Double?
    }

    /// 堆叠顺序 (自下而上): 股息(内→外) → 利息(内→外) → 资本利得(内→外);
    /// 境内暖色 / 境外冷色, 同侧内 股息→利息→资本利得 由深到浅.
    private static let cashSeries: [CashSeries] = [
        CashSeries(name: "股息·境内",     color: Color(hue: 0.05, saturation: 0.85, brightness: 0.78)) { $0.dividendDomestic },
        CashSeries(name: "股息·境外",     color: Color(hue: 0.58, saturation: 0.85, brightness: 0.72)) { $0.dividendOverseas },
        CashSeries(name: "利息·境内",     color: Color(hue: 0.05, saturation: 0.65, brightness: 0.92)) { $0.interestDomestic },
        CashSeries(name: "利息·境外",     color: Color(hue: 0.58, saturation: 0.60, brightness: 0.88)) { $0.interestOverseas },
        CashSeries(name: "资本利得·境内", color: Color(hue: 0.05, saturation: 0.35, brightness: 1.00)) { $0.capitalGainDomestic },
        CashSeries(name: "资本利得·境外", color: Color(hue: 0.58, saturation: 0.30, brightness: 1.00)) { $0.capitalGainOverseas },
    ]

    private struct CashBar: Identifiable {
        let id: String
        let label: String
        let series: String
        let v: Double
    }

    private var cashCarouselBars: [CashBar] {
        var out: [CashBar] = []
        for (i, c) in filtered.enumerated() {
            let label = labels[i]
            for s in Self.cashSeries {
                if let v = s.value(c.report), v != 0 {
                    out.append(CashBar(id: "\(s.name)|\(label)", label: label, series: s.name, v: v))
                }
            }
        }
        return out
    }

    private var cashChart: some View {
        let bars = cashCarouselBars
        return Chart {
            ForEach(bars) { b in
                BarMark(
                    x: .value("季度", b.label),
                    y: .value("金额", b.v),
                    width: .ratio(0.62)
                )
                .foregroundStyle(by: .value("类别", b.series))
                .cornerRadius(2)
            }
            hoverRule
        }
        .chartForegroundStyleScale(
            domain: Self.cashSeries.map(\.name),
            range: Self.cashSeries.map(\.color)
        )
        .chartLegend(.hidden)
        .chartYAxis {
            AxisMarks(position: .leading) { value in
                AxisGridLine()
                AxisValueLabel {
                    if let d = value.as(Double.self) {
                        Text(amount(d))
                    }
                }
            }
        }
    }

    private var cashLegend: some View {
        VStack(alignment: .leading, spacing: 4) {
            // 按类别逐行展示 (与堆叠顺序一致): 左境内(暖) 右境外(冷).
            legendRow(title: "股息",     names: ["股息·境内", "股息·境外"])
            legendRow(title: "利息",     names: ["利息·境内", "利息·境外"])
            legendRow(title: "资本利得", names: ["资本利得·境内", "资本利得·境外"])
            let dom = filtered.reduce(0.0) {
                $0 + ($1.report.interestDomestic ?? 0) + ($1.report.dividendDomestic ?? 0) + ($1.report.capitalGainDomestic ?? 0)
            }
            let ovs = filtered.reduce(0.0) {
                $0 + ($1.report.interestOverseas ?? 0) + ($1.report.dividendOverseas ?? 0) + ($1.report.capitalGainOverseas ?? 0)
            }
            Text("当前范围合计: 境内 \(amount(dom)) / 境外 \(amount(ovs)) / 全部 \(amount(dom + ovs))")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private var capitalLegend: some View {
        HStack(spacing: 12) {
            ForEach(Self.capitalLayers, id: \.name) { l in
                HStack(spacing: 4) {
                    RoundedRectangle(cornerRadius: 2)
                        .fill(l.color)
                        .frame(width: 10, height: 10)
                    Text(l.name)
                }
            }
        }
        .font(.caption)
    }

    private var investLegend: some View {
        HStack(spacing: 12) {
            HStack(spacing: 4) {
                RoundedRectangle(cornerRadius: 2)
                    .fill(Color.gray.opacity(0.65))
                    .frame(width: 10, height: 10)
                Text("一次投资")
            }
            HStack(spacing: 4) {
                RoundedRectangle(cornerRadius: 2)
                    .fill(Color.accentColor.opacity(0.85))
                    .frame(width: 10, height: 10)
                Text("二次投资")
            }
        }
        .font(.caption)
    }

    private func legendRow(title: String, names: [String]) -> some View {
        HStack(spacing: 10) {
            Text(title)
                .foregroundStyle(.secondary)
            ForEach(names, id: \.self) { name in
                HStack(spacing: 4) {
                    RoundedRectangle(cornerRadius: 2)
                        .fill(Self.cashSeries.first { $0.name == name }?.color ?? .gray)
                        .frame(width: 10, height: 10)
                    Text(name.hasSuffix("境内") ? "境内" : "境外")
                }
            }
        }
        .font(.caption)
    }

    // MARK: - 图 3 · 投资流入 (一次投资 + 二次投资 堆叠柱, 柱顶标新增投入合计)

    private struct InvRow: Identifiable {
        let id: String
        let label: String
        let primary: Double
        let secondary: Double
        let total: Double
    }

    private var investRows: [InvRow] {
        filtered.enumerated().compactMap { i, c in
            guard let total = c.newInvestment, total != 0 else { return nil }
            let p = max(c.primaryInvestment ?? 0, 0)
            // 拆分不完整时 (primary+secondary 与总额不符) 以总额为兜底, 避免柱子缺斤短两.
            let s = c.secondaryInvestment.map { max(0, $0) } ?? max(total - p, 0)
            return InvRow(id: labels[i], label: labels[i], primary: p, secondary: s, total: total)
        }
    }

    private var investChart: some View {
        let rows = investRows
        return Group {
            if rows.isEmpty {
                chartNote("当前范围没有新增投入记录 (本金未发生变化)")
            } else {
                let maxV = max(rows.map { $0.primary + $0.secondary }.max() ?? 0, 1)
                Chart {
                    ForEach(rows) { r in
                        BarMark(x: .value("季度", r.label), y: .value("一次投资", r.primary), width: .ratio(0.55))
                            .foregroundStyle(Color.gray.opacity(0.65))
                            .cornerRadius(3)
                    }
                    ForEach(rows) { r in
                        BarMark(x: .value("季度", r.label), y: .value("二次投资", r.secondary), width: .ratio(0.55))
                            .foregroundStyle(Color.accentColor.opacity(0.85))
                            .cornerRadius(3)
                    }
                    ForEach(rows) { r in
                        BarMark(x: .value("季度", r.label), y: .value("合计", 0), width: .ratio(0.55))
                            .opacity(0)
                            .annotation(position: .top) {
                                Text(amount(r.total))
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                    }
                    hoverRule
                }
                .chartYScale(domain: 0...(maxV * 1.2))
                .chartYAxis {
                    AxisMarks(position: .leading) { value in
                        AxisGridLine()
                        AxisValueLabel {
                            if let d = value.as(Double.self) {
                                Text(amount(d))
                            }
                        }
                    }
                }
                .chartLegend(.hidden)
            }
        }
    }

    // MARK: - 图 4 · 季度收益 (滚动年化点 + 95% CI 误差棒 + 均值虚线)

    private struct AnnPt: Identifiable {
        let id: String
        let label: String
        let v: Double    // 年化
        let lo: Double?  // CI 下界 (ciMinus)
        let hi: Double?  // CI 上界 (ciPlus)
    }

    private var annualPoints: [AnnPt] {
        filtered.enumerated().compactMap { i, c in
            guard let v = c.annualizedRate else { return nil }
            return AnnPt(id: labels[i], label: labels[i], v: v, lo: c.ciMinus, hi: c.ciPlus)
        }
    }

    private var annualChart: some View {
        let pts = annualPoints
        // 当前运行均值 = 全量序列最后一个非 nil 值 (不受 12 季过滤影响, 供对照).
        let runningMean = columns.last(where: { $0.meanRate != nil })?.meanRate
        return Group {
            if pts.isEmpty {
                chartNote("暂无年化收益率数据")
            } else {
                Chart {
                    if let m = runningMean {
                        RuleMark(y: .value("均值", m))
                            .foregroundStyle(.secondary.opacity(0.6))
                            .lineStyle(StrokeStyle(lineWidth: 1, dash: [5, 4]))
                            .annotation(position: .top, alignment: .trailing) {
                                Text("均值 \(pct(m))")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                    }
                    ForEach(pts) { p in
                        if let lo = p.lo, let hi = p.hi {
                            RectangleMark(
                                x: .value("季度", p.label),
                                yStart: .value("下界", lo),
                                yEnd: .value("上界", hi),
                                width: .fixed(8)
                            )
                            .foregroundStyle(.secondary.opacity(0.45))
                            .cornerRadius(2)
                        }
                    }
                    ForEach(pts) { p in
                        PointMark(x: .value("季度", p.label), y: .value("年化", p.v))
                            .foregroundStyle(.purple)
                            .symbolSize(46)
                    }
                    // 相邻数据点用同色虚线相连: 每对相邻点两两共用 series, 缺失季度自然断开.
                    ForEach(Array(zip(pts, pts.dropFirst())), id: \.0.id) { a, b in
                        ForEach([a, b]) { p in
                            LineMark(
                                x: .value("季度", p.label),
                                y: .value("年化", p.v),
                                series: .value("连接", b.id)
                            )
                        }
                        .foregroundStyle(.purple.opacity(0.55))
                        .lineStyle(StrokeStyle(lineWidth: 1.5, dash: [5, 4]))
                    }
                    hoverRule
                }
                .chartYScale(domain: .automatic(includesZero: true))
                .chartYAxis {
                    AxisMarks(position: .leading) { value in
                        AxisGridLine()
                        AxisValueLabel {
                            if let d = value.as(Double.self) {
                                Text("\(Int(d * 100))%")
                            }
                        }
                    }
                }
            }
        }
    }

    // MARK: - 图 5 · 本金与市值 (总市值结构堆积面积图)
    // 自下而上: 累计股息 → 累计利息 → 累计已实现资本利得 → 本金 (= 总成本, 纯平滑堆积, 与改动前一致);
    // 最上面那条「未实现资本利得」带另画一条独立序列, 两端锚在总成本线上:
    //   为正 → 叠在总成本之上 (色块上界 = 总市值);
    //   为负 → 压在总成本之内 (盖掉本金层顶部那一截, 色块上界 = 总成本).
    // 这样负值永远不会进入堆叠 (进去就会被甩到 0 轴以下), Y 轴恒从 0 起; 详见 AGENTS.md §13.

    private struct StackLayer {
        let name: String
        let color: Color
        let value: (QuarterComputed) -> Double?
    }

    /// 配色: 统一大地暖色系, 相邻层强对比 (金→青灰→橙→砖红→奶油), 底部深 = 本金压舱, 顶部浅 = 未实现资本利得.
    private static let capitalLayers: [StackLayer] = [
        StackLayer(name: "累计股息",           color: Color(hue: 0.115, saturation: 0.72, brightness: 0.94)) { $0.cumDividend },
        StackLayer(name: "累计利息",           color: Color(hue: 0.52,  saturation: 0.50, brightness: 0.55)) { $0.cumInterest },
        StackLayer(name: "累计已实现资本利得", color: Color(hue: 0.065, saturation: 0.78, brightness: 0.86)) { $0.cumRealizedGain },
        StackLayer(name: "本金",               color: Color(hue: 0.015, saturation: 0.70, brightness: 0.50)) { $0.principal },
        StackLayer(name: "未实现资本利得",     color: Color(hue: 0.11,  saturation: 0.38, brightness: 1.00)) { $0.unrealizedGain },
    ]

    /// 堆叠用前三/四层 = 上面数组的前四项 (它们的和 ≡ 总成本); 未实现带不走堆叠.
    private static let capitalStackLayers: [StackLayer] = Array(capitalLayers[0..<4])

    /// 本金参考线: **亮绿**粗虚线 (高饱和高亮度, 压在砖红/橙/青灰/浅黄上都跳得出来; 深色背景下同样醒目), 不进图例.
    private static let principalBaselineColor = Color(hue: 0.30, saturation: 0.82, brightness: 0.95)

    /// 图 5 的 X 轴用**数值轴**(季度序号), 不是类别轴: 只有这样才放得下"两个季度之间"的交点采样点.
    private func capitalX(_ i: Int) -> Double { Double(i) }

    private struct Stratum: Identifiable {
        let id: String
        let label: String
        let layer: String
        let x: Double
        let v: Double
    }

    /// 可画的一列 = 总市值与总成本都填了; 缺任一个 → 整列跳过 (层与点都不画).
    private func capitalColumn(_ c: QuarterComputed) -> (mv: Double, cost: Double)? {
        guard let mv = c.report.marketValue, let cost = c.report.totalCost, c.principal != nil else { return nil }
        return (mv, cost)
    }

    private var capStrata: [Stratum] {
        var out: [Stratum] = []
        for (i, c) in filtered.enumerated() where capitalColumn(c) != nil {
            for l in Self.capitalStackLayers {
                if let v = l.value(c) {
                    out.append(Stratum(id: "\(l.name)|\(labels[i])", label: labels[i], layer: l.name,
                                       x: capitalX(i), v: v))
                }
            }
        }
        return out
    }

    /// 未实现带的一个采样点: 上下就是「总市值」与「总成本」两条折线.
    private struct GainPoint: Identifiable {
        let id: String
        let x: Double
        let mv: Double
        let cost: Double
    }

    /// 未实现带 = 各季度点 + **交点采样点**.
    /// 两条直线在两个季度之间相交时, 把交点也插成一个采样点 —— 于是带子在那里厚度真正归零, 然后翻到另一侧.
    /// (类别轴做不到: AreaMark 会把每个点上的上下界按大小归一化再各自插值, 两点之间永远不会相交.)
    private var capitalGainPoints: [GainPoint] {
        var cols: [(x: Double, mv: Double, cost: Double)] = []
        for (i, c) in filtered.enumerated() {
            guard let col = capitalColumn(c) else { continue }
            cols.append((capitalX(i), col.mv, col.cost))
        }
        var out: [GainPoint] = []
        for (k, p) in cols.enumerated() {
            out.append(GainPoint(id: "q\(k)", x: p.x, mv: p.mv, cost: p.cost))
            guard k + 1 < cols.count else { continue }
            let q = cols[k + 1]
            let d0 = p.mv - p.cost, d1 = q.mv - q.cost
            guard d0.sign != d1.sign, d0 != 0, d1 != 0 else { continue }
            let t = d0 / (d0 - d1)
            guard t > 0, t < 1 else { continue }
            let v = p.mv + (q.mv - p.mv) * t
            out.append(GainPoint(id: "x\(k)", x: p.x + (q.x - p.x) * t, mv: v, cost: v))
        }
        return out
    }

    /// 本金参考线 = 「本金」字段本身 (离横轴的距离 = 本金金额), 不是本金层的下边缘.
    private struct BaselinePoint: Identifiable {
        let id: String
        let x: Double
        let v: Double
    }

    private var principalBaseline: [BaselinePoint] {
        filtered.enumerated().compactMap { i, c in
            guard capitalColumn(c) != nil, let p = c.principal else { return nil }
            return BaselinePoint(id: labels[i], x: capitalX(i), v: p)
        }
    }

    /// 数值轴上的季度标签: 只有整数刻度才是真季度 (交点是小数刻度, 不该出现标签).
    private func quarterLabel(at x: Double) -> String? {
        let i = Int(x.rounded())
        guard abs(x - Double(i)) < 0.001, i >= 0, i < labels.count else { return nil }
        return labels[i]
    }

    /// 数值轴范围: 左右各留半个槽, 免得首末季度的标签被切掉.
    private var capitalXDomain: ClosedRange<Double> {
        let hi = Double(max(filtered.count - 1, 0))
        return -0.45...(hi + 0.45)
    }

    /// Y 轴上界 = 色块最高处 (总市值 / 总成本 取大者) × 1.15; 下界恒 0.
    private var capitalTop: Double {
        var hi = 0.0
        for c in filtered {
            guard let col = capitalColumn(c) else { continue }
            hi = max(hi, max(col.mv, col.cost))
        }
        return max(hi * 1.15, 1)
    }

    /// 末尾读数, 固定放图外顶边 (无论该季未实现资本利得为正还是为负, 位置一致).
    private var capitalReadout: String? {
        guard let last = filtered.last(where: { capitalColumn($0) != nil }),
              let col = capitalColumn(last) else { return nil }
        return "总市值 \(amount(col.mv)) · 总成本 \(amount(col.cost))"
    }

    private var capitalChart: some View {
        let strata = capStrata
        let gainPoints = capitalGainPoints
        let baseline = principalBaseline
        return Group {
            if strata.isEmpty {
                chartNote("暂无市值数据")
            } else {
                Chart {
                    // 四层正项堆积: 相加 = 总成本 (x 用季度序号, 与数值轴一致).
                    ForEach(strata) { s in
                        AreaMark(
                            x: .value("季度序号", s.x),
                            y: .value("金额", s.v)
                        )
                        .foregroundStyle(by: .value("构成", s.layer))
                        .interpolationMethod(.linear)
                    }
                    // 未实现带: 独立序列, 上下边 = 总市值 / 总成本 两条折线 (含交点采样点, 翻符号处厚度归零).
                    ForEach(gainPoints) { p in
                        AreaMark(
                            x: .value("季度序号", p.x),
                            yStart: .value("总市值", p.mv),
                            yEnd: .value("总成本", p.cost)
                        )
                        .foregroundStyle(Self.capitalLayers[4].color)
                        .interpolationMethod(.linear)
                    }
                    // 本金参考线 (常显, 不进图例; 值 = 「本金」字段本身).
                    ForEach(baseline) { p in
                        LineMark(
                            x: .value("季度序号", p.x),
                            y: .value("本金", p.v)
                        )
                        .foregroundStyle(Self.principalBaselineColor)
                        .lineStyle(StrokeStyle(lineWidth: 2.5, dash: [5, 3]))
                        .interpolationMethod(.linear)
                    }
                    capitalHoverRule
                }
                .chartForegroundStyleScale(
                    domain: Self.capitalStackLayers.map(\.name),
                    range: Self.capitalStackLayers.map(\.color)
                )
                .chartYScale(domain: 0...capitalTop)
                .chartXScale(domain: capitalXDomain)
                .chartXAxis {
                    AxisMarks(values: filtered.indices.map { capitalX($0) }) { value in
                        AxisGridLine()
                        AxisValueLabel {
                            if let d = value.as(Double.self), let text = quarterLabel(at: d) {
                                Text(text)
                            }
                        }
                    }
                }
                .chartYAxis {
                    AxisMarks(position: .leading) { value in
                        AxisGridLine()
                        AxisValueLabel {
                            if let d = value.as(Double.self) {
                                Text(amount(d))
                            }
                        }
                    }
                }
                .chartLegend(.hidden)
                .chartOverlay(alignment: .topTrailing) { _ in
                    if let readout = capitalReadout {
                        Text(readout)
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.secondary)
                            .padding(.trailing, 6)
                            .allowsHitTesting(false)
                    }
                }
            }
        }
    }

    // MARK: - 悬停

    @ChartContentBuilder
    private var hoverRule: some ChartContent {
        if let h = hoverLabel {
            RuleMark(x: .value("悬停", h))
                .foregroundStyle(.secondary.opacity(0.5))
                .lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 3]))
        }
    }

    /// 图 5 的悬停竖线: 它用数值 x, 得把悬停到的季度标签折算回序号.
    @ChartContentBuilder
    private var capitalHoverRule: some ChartContent {
        if let h = hoverLabel, let i = labels.firstIndex(of: h) {
            RuleMark(x: .value("悬停", capitalX(i)))
                .foregroundStyle(.secondary.opacity(0.5))
                .lineStyle(StrokeStyle(lineWidth: 1, dash: [4, 3]))
        }
    }

    @ViewBuilder
    private func hoverSummary(_ label: String) -> some View {
        if let idx = labels.firstIndex(of: label) {
            let c = filtered[idx]
            switch tab {
            case .returnCurve:
                hoverLine(title: label, rows: [("累计净总回报率", pct(c.cumNetReturnRate), true)])
            case .cashFlow:
                let r = c.report
                let total = (r.interestDomestic ?? 0) + (r.interestOverseas ?? 0)
                    + (r.dividendDomestic ?? 0) + (r.dividendOverseas ?? 0)
                    + (r.capitalGainDomestic ?? 0) + (r.capitalGainOverseas ?? 0)
                hoverLine(title: label, rows: [
                    ("股息·境内", amount(r.dividendDomestic), false),
                    ("股息·境外", amount(r.dividendOverseas), false),
                    ("利息·境内", amount(r.interestDomestic), false),
                    ("利息·境外", amount(r.interestOverseas), false),
                    ("资本利得·境内", amount(r.capitalGainDomestic), false),
                    ("资本利得·境外", amount(r.capitalGainOverseas), false),
                    ("合计", amount(total), true),
                ])
            case .investment:
                hoverLine(title: label, rows: [
                    ("新增投入", amount(c.newInvestment), true),
                    ("一次投资", amount(c.primaryInvestment), false),
                    ("二次投资", amount(c.secondaryInvestment), false),
                    ("二次占比", pct(c.secondaryShare), false),
                ])
            case .quarterReturn:
                let ciText: String? = {
                    guard let lo = c.ciMinus, let hi = c.ciPlus else { return nil }
                    return "\(pct(lo)) ~ \(pct(hi))"
                }()
                hoverLine(title: label, rows: [
                    ("滚动年化", pct(c.annualizedRate), true),
                    ("95% 区间", ciText ?? "— (需满 12 季)", false),
                    ("运行均值", pct(c.meanRate), false),
                    ("标准差", pct(c.stdDev), false),
                ])
            case .capital:
                hoverLine(title: label, rows: [
                    ("累计股息", amount(c.cumDividend), false),
                    ("累计利息", amount(c.cumInterest), false),
                    ("累计已实现资本利得", amount(c.cumRealizedGain), false),
                    ("本金", amount(c.principal), false),
                    ("未实现资本利得", amount(c.unrealizedGain), false),
                    ("总成本", amount(c.report.totalCost), false),
                    ("总市值", amount(c.report.marketValue), true),
                ])
            }
        }
    }

    private func hoverLine(title: String, rows: [(String, String, Bool)]) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 14) {
            Text(title)
                .font(.caption.weight(.semibold))
            ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                HStack(spacing: 4) {
                    Text(row.0)
                        .foregroundStyle(.secondary)
                    Text(row.1)
                        .font(row.2 ? .caption.weight(.semibold) : .caption)
                        .monospacedDigit()
                }
            }
        }
    }

    // MARK: - 小工具

    private struct XY: Hashable {
        let idx: Int
        let label: String
        let y: Double
    }

    /// 配对非 nil 点 (nil 被剔除 → 断线 / 不画).
    private func pairPoints(_ labels: [String], _ values: [Double?]) -> [XY] {
        var out: [XY] = []
        for (i, v) in values.enumerated() where i < labels.count {
            if let v {
                out.append(XY(idx: i, label: labels[i], y: v))
            }
        }
        return out
    }

    /// 把点列按“索引是否相邻”切成连续段 (缺数据的季度形成断口).
    private func splitRuns(_ pts: [XY]) -> [[XY]] {
        var runs: [[XY]] = []
        for p in pts {
            if var last = runs.last, last.last?.idx == p.idx - 1 {
                last.append(p)
                runs[runs.count - 1] = last
            } else {
                runs.append([p])
            }
        }
        return runs
    }

    private func chartNote(_ text: String) -> some View {
        Text(text)
            .font(.callout)
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: - 格式化

    /// 金额自适应: ≥1亿 → x.xx亿; ≥1万 → x.x万; 否则取整 (CNY).
    private func amount(_ v: Double?) -> String {
        guard let v else { return "—" }
        let a = abs(v)
        if a >= 100_000_000 { return String(format: "%.2f亿", v / 100_000_000) }
        if a >= 10_000 { return String(format: "%.1f万", v / 10_000) }
        if a >= 1 || v == 0 { return String(format: "%.0f", v) }
        return String(format: "%.2f", v)
    }

    private func pct(_ v: Double?) -> String {
        v.map { String(format: "%.1f%%", $0 * 100) } ?? "—"
    }
}

// MARK: - 悬停位置捕获

private struct ChartHoverModifier: ViewModifier {
    let labels: [String]
    /// 数值 x 轴 (图 5 用季度序号) 时 value(atX:) 返回 Double, 得折算到最近的季度; 其余四张图是类别轴.
    var numeric = false
    @Binding var hovered: String?

    func body(content: Content) -> some View {
        content.chartOverlay { proxy in
            GeometryReader { geo in
                Rectangle()
                    .fill(.clear)
                    .contentShape(Rectangle())
                    .onContinuousHover { phase in
                        switch phase {
                        case .active(let location):
                            guard let plotFrame = proxy.plotFrame else { return }
                            let origin = geo[plotFrame].origin
                            let x = location.x - origin.x
                            if numeric {
                                if let d: Double = proxy.value(atX: x), !labels.isEmpty {
                                    let i = min(max(Int(d.rounded()), 0), labels.count - 1)
                                    hovered = labels[i]
                                }
                            } else if let label: String = proxy.value(atX: x) {
                                hovered = label
                            }
                        case .ended:
                            hovered = nil
                        }
                    }
            }
        }
    }
}

private extension View {
    func withHover(labels: [String], hovered: Binding<String?>, numeric: Bool = false) -> some View {
        modifier(ChartHoverModifier(labels: labels, numeric: numeric, hovered: hovered))
    }
}
