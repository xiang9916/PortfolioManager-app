import SwiftUI
import PortfolioCore

/// 能力4 (重做): 财务分析 — 结构 1:1 对齐 portfolio.xlsx.
/// 顶部: 统计图面板 (FinancialChartPanel, 5 张图分段切换); 下方: 逐季度数据网格, 一列 = 一个季度.
/// 9 个手动字段 (总市值 / 总成本 / 境内·境外利息 / 股息 / 资本利得 / (红利税、资本利得税))
/// 在网格内联编辑, 录入后仍可修改; 其余行全部由 QuarterlyMetrics 公式链自动派生.
/// 「总市值」「总成本」两格另有「填入当前值」按钮 (空格常显、已有值悬停出现, 见 docs/adr/0001).
///
/// 网格: 最左侧「字段名列」冻结不动, 只有数据列横向滚动; 数据列按季末**降序**排列 (最新季度在最左,
/// 期初基准列在最右), 因此新增季度总是出现在最左侧。派生指标本身仍按季末升序计算 (见 QuarterlyMetrics)。
public struct FinancialAnalysisView: View {
    @Bindable var store: AppStore

    /// 所有单元格右侧统一预留的槽位：给「总市值 / 总成本」两行的填入按钮用。
    /// 统一预留（而不是只给那两行）是为了让同列数字仍然对齐在同一竖直线上。
    private let cellTrailingInset: CGFloat = 23

    /// 内联编辑的文本草稿 ((季度, 字段) → 原始输入). 显示以草稿为准, 不与格式化互相打架.
    @State private var drafts: [CellKey: String] = [:]
    /// 表头日期编辑草稿 (periodEnd → 原始输入).
    @State private var dateDrafts: [String: String] = [:]
    /// 鼠标悬停的单元格 —— 「总市值/总成本」两行已有值时，填入按钮只在悬停时出现（Q10=B）。
    @State private var hoveredCell: CellKey?

    private let labelWidth: CGFloat = 212
    private let columnWidth: CGFloat = 150
    private let rowHeight: CGFloat = 30
    /// 表头高度与区块标题行高度 —— 冻结列与滚动区靠这两个常量保证逐行对齐.
    private let headerHeight: CGFloat = 40
    private let sectionHeight: CGFloat = 32
    /// 数据区内容宽度 (所有季度列 + 尾部 16pt 余量), 区块标题的灰色横幅按它拉满.
    private var dataWidth: CGFloat { CGFloat(displayColumns.count) * columnWidth + 16 }

    public var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                FinancialChartPanel(columns: columns, hideNumbers: store.hideNumbers)
                quarterlyCard
            }
            .padding()
        }
        .navigationTitle("财务分析")
        // 右上角: 模块专属按钮 ｜ 全局按钮.
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                Button { store.addQuarter() } label: { Label("添加季度", systemImage: "plus") }
                    .help("在最左侧新增一个季度列 (自动取下一个季末日期)")
                ToolbarDivider()
            }
            GlobalToolbarContent(store: store)
        }
        .onAppear { syncDrafts() }
        .onChange(of: store.quarterlyReports.map(\.periodEnd)) { _, _ in syncDrafts() }
    }

    /// 派生列 (按季末升序 —— 公式链与统计图都依赖这个顺序).
    private var columns: [QuarterComputed] { QuarterlyMetrics.compute(store.quarterlyReports) }

    /// 网格展示顺序: 季末降序, 最新季度在最左, 新增列即出现在左侧.
    private var displayColumns: [QuarterComputed] { columns.reversed() }

    // MARK: - 逐季度数据区

    private var quarterlyCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("逐季度数据").font(.headline)
            if columns.isEmpty {
                ContentUnavailableView {
                    Label("暂无季度数据", systemImage: "tablecells")
                } description: {
                    Text("点右上角「添加季度」建立第一列 (期初: 总市值 = 总成本)，之后每个季度结束后添加一列 (加在最左侧) 并补录 9 项数据。")
                } actions: {
                    Button("添加第一个季度") { store.addQuarter() }
                        .buttonStyle(.borderedProminent)
                }
            } else {
                // 冻结列 + 横向滚动数据区: 两列各自独立成列, 行高由上面的常量统一, 因此逐行天然对齐.
                HStack(alignment: .top, spacing: 0) {
                    frozenColumn
                    ScrollView(.horizontal, showsIndicators: true) {
                        dataArea
                    }
                }
                .padding(.vertical, 4)
                legend
            }
        }
        .padding()
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 12))
    }

    // MARK: - 左侧冻结列 (字段名, 横向滚动时始终可见)

    private var frozenColumn: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("季度截止日")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
                .padding(.horizontal, 8)
                .frame(width: labelWidth, height: headerHeight, alignment: .leading)
                .padding(.bottom, 6)
            ForEach(rows) { row in
                if row.isSection {
                    frozenSectionCell(row.label)
                } else {
                    rowLabel(row)
                }
            }
        }
        .overlay(alignment: .trailing) {
            Rectangle()
                .fill(Color.primary.opacity(0.12))
                .frame(width: 1)
        }
    }

    private func frozenSectionCell(_ title: String) -> some View {
        Text(title)
            .font(.subheadline.weight(.bold))
            .lineLimit(1)
            .padding(.horizontal, 8)
            .frame(width: labelWidth, height: sectionHeight, alignment: .leading)
            .background(Color.gray.opacity(0.14))
    }

    // MARK: - 横向滚动的数据区 (表头 + 单元格)

    private var dataArea: some View {
        VStack(alignment: .leading, spacing: 0) {
            headerDates
            ForEach(rows) { row in
                if row.isSection {
                    Color.gray.opacity(0.14)
                        .frame(width: dataWidth, height: sectionHeight)
                } else {
                    dataCells(row)
                }
            }
        }
    }

    // MARK: 表头 (季度日期, 可编辑 / 右键删除)

    private var headerDates: some View {
        HStack(spacing: 0) {
            ForEach(displayColumns, id: \.report.periodEnd) { c in
                let pe = c.report.periodEnd
                VStack(spacing: 1) {
                    Text(QuarterlyMetrics.quarterLabel(pe))
                        .font(.caption.weight(.bold))
                        .foregroundStyle(.secondary)
                    TextField("yyyy-MM-dd", text: dateBinding(pe))
                        .textFieldStyle(.plain)
                        .font(.system(size: 11, design: .monospaced))
                        .multilineTextAlignment(.center)
                        .frame(width: columnWidth - 20)
                        .onSubmit { commitDateEdit(pe) }
                }
                .frame(width: columnWidth, height: headerHeight)
                .background(Color.primary.opacity(0.04), in: RoundedRectangle(cornerRadius: 4))
                .contextMenu {
                    Button("删除该季度", role: .destructive) {
                        store.deleteQuarter(periodEnd: pe)
                    }
                }
                .help("编辑日期或右键删除该季度列")
            }
            Color.clear.frame(width: 16)
        }
        .padding(.bottom, 6)
    }

    private func dateBinding(_ pe: String) -> Binding<String> {
        Binding(
            get: { dateDrafts[pe] ?? pe },
            set: { dateDrafts[pe] = $0 }
        )
    }

    private func commitDateEdit(_ oldEnd: String) {
        let newText = (dateDrafts[oldEnd] ?? oldEnd).trimmingCharacters(in: .whitespaces)
        store.renameQuarter(from: oldEnd, to: newText)
    }

    // MARK: 数据行

    private func dataCells(_ row: GridRow) -> some View {
        HStack(spacing: 0) {
            ForEach(displayColumns, id: \.report.periodEnd) { c in
                if store.hideNumbers {
                    maskedCell(emphasis: row.emphasis)
                } else if let field = row.manual {
                    manualCell(c.report.periodEnd, field)
                } else {
                    computedCell(row.value(c), emphasis: row.emphasis)
                }
            }
            Color.clear.frame(width: 16)
        }
        .frame(height: rowHeight)
        .overlay(alignment: .bottom) {
            Rectangle().fill(Color.primary.opacity(0.06)).frame(height: 1)
        }
    }

    /// 数字隐藏时替代单元格的 "***" (与计算单元格相同的排版).
    private func maskedCell(emphasis: Bool) -> some View {
        Text(PrivacyStyle.masked)
            .font(.system(size: 12, weight: emphasis ? .semibold : .regular, design: .monospaced))
            .foregroundStyle(.secondary)
            .frame(width: columnWidth - 7 - cellTrailingInset, alignment: .trailing)
            .padding(.leading, 7)
            .padding(.trailing, cellTrailingInset)
    }

    private func rowLabel(_ row: GridRow) -> some View {
        HStack(spacing: 4) {
            if row.manual != nil {
                Image(systemName: "pencil.and.list.clipboard")
                    .font(.system(size: 8))
                    .foregroundStyle(.tertiary)
            }
            Text(row.label)
                .font(.system(size: 12, weight: row.emphasis ? .semibold : .regular))
                .foregroundStyle(row.emphasis ? .primary : .secondary)
                .lineLimit(1)
            Spacer(minLength: 0)
        }
        .padding(.leading, 8 + CGFloat(row.indent) * 14)
        .padding(.trailing, 8)
        .frame(width: labelWidth, height: rowHeight, alignment: .leading)
        .overlay(alignment: .bottom) {
            Rectangle().fill(Color.primary.opacity(0.06)).frame(height: 1)
        }
    }

    /// 手动字段单元格: 蓝底内联输入框, 值解析后实时驱动派生行, 落盘防抖.
    /// 「总市值 / 总成本」两格右侧另有「填入当前值」按钮（Q6=C）：空格常显、已有值悬停才出现。
    private func manualCell(_ pe: String, _ field: QuarterlyField) -> some View {
        let key = CellKey(periodEnd: pe, field: field)
        let fillValue = store.quarterlyFillValue(periodEnd: pe, field: field)
        let isEmpty = (drafts[key] ?? "").trimmingCharacters(in: .whitespaces).isEmpty
        let showFill = fillValue != nil && (isEmpty || hoveredCell == key)
        // 按钮刻意做成 TextField 的**兄弟节点**而不是 overlay：TextField 底下是 NSTextField，
        // overlay 上的按钮有被它吞掉点击的风险；兄弟节点占住右侧预留槽位，点击必然落在按钮上。
        // 槽位恒定（cellTrailingInset），所以悬停时不会发生布局跳动。
        return HStack(spacing: 0) {
            TextField("—", text: Binding(
                get: { drafts[key] ?? "" },
                set: { text in
                    drafts[key] = text
                    store.updateQuarterlyField(periodEnd: pe, field: field, value: parseNumber(text))
                }
            ))
            .textFieldStyle(.plain)
            .font(.system(size: 12, design: .monospaced))
            .multilineTextAlignment(.trailing)
            .frame(width: columnWidth - 7 - cellTrailingInset, height: rowHeight - 8, alignment: .trailing)
            .padding(.leading, 7)
            .help(field.label + " · 手动录入, 可随时修改"
                  + (fillValue != nil ? "；右侧按钮可填入当前值" : ""))

            ZStack {
                if fillValue != nil {
                    Button { fill(periodEnd: pe, field: field, key: key) } label: {
                        Image(systemName: "arrow.down.to.line")
                            .font(.system(size: 9, weight: .semibold))
                            .frame(width: 16, height: 16)
                            .background(Color.accentColor.opacity(0.18), in: RoundedRectangle(cornerRadius: 4))
                            .contentShape(RoundedRectangle(cornerRadius: 4))
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(Color.accentColor)
                    .opacity(showFill ? 1 : 0)
                    .allowsHitTesting(showFill)
                    .help(fillHelp(field, value: fillValue))
                }
            }
            .frame(width: cellTrailingInset, height: rowHeight - 8)
        }
        .frame(width: columnWidth, height: rowHeight)
        .background(Color.accentColor.opacity(0.12), in: RoundedRectangle(cornerRadius: 4))
        .onHover { hovering in
            if hovering { hoveredCell = key }
            else if hoveredCell == key { hoveredCell = nil }
        }
    }

    /// 把当前的总市值/总成本填进这一格（走与手输相同的落盘路径，派生行立刻跟着重算）。
    private func fill(periodEnd: String, field: QuarterlyField, key: CellKey) {
        guard let v = store.applyQuarterlyFill(periodEnd: periodEnd, field: field) else { return }
        drafts[key] = formatDraft(v)
    }

    private func fillHelp(_ field: QuarterlyField, value: Double?) -> String {
        let v = value.map { String(format: "¥%.2f", $0) } ?? "—"
        let what = field == .marketValue ? "总市值" : "总成本"
        return "填入当前的\(what) \(v)\n⚠️ 填的是当下实时值，不是该季末的历史值（App 不记录历史总市值）"
    }

    private func computedCell(_ text: String, emphasis: Bool) -> some View {
        Text(text.isEmpty ? "—" : text)
            .font(.system(size: 12, weight: emphasis ? .semibold : .regular, design: .monospaced))
            .foregroundStyle(styleFor(text))
            .frame(width: columnWidth - 7 - cellTrailingInset, alignment: .trailing)
            .padding(.leading, 7)
            .padding(.trailing, cellTrailingInset)
    }

    private func isNegative(_ s: String) -> Bool { s.hasPrefix("-") || s.hasPrefix("−") }

    private func styleFor(_ text: String) -> AnyShapeStyle {
        if text.isEmpty { return AnyShapeStyle(.quaternary) }
        if isNegative(text) { return AnyShapeStyle(Color.red) }
        return AnyShapeStyle(.primary)
    }

    // MARK: - 图例

    private var legend: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                RoundedRectangle(cornerRadius: 3)
                    .fill(Color.accentColor.opacity(0.35))
                    .frame(width: 22, height: 12)
                Text("手动录入 (9 项, 录入后仍可编辑): 总市值 · 总成本 · 利息/股息/资本利得的境内与境外 · (红利税、资本利得税)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Text("「总市值」「总成本」两格：空着时右侧的 ↓ 按钮常显，已有值时悬停才出现 —— 点一下填入**当前**的总市值 / 总成本（是当下实时值，不是该季末的历史值）。")
                .font(.caption)
                .foregroundStyle(.tertiary)
            Text("其余行均由公式自动计算; 表头日期可点击编辑, 右键表头删除该季度; 最右一列为期初基准列 (总市值 = 总成本, 两格同填总成本); 数据列从近到早排列, 新季度总是加在最左侧。")
                .font(.caption)
                .foregroundStyle(.tertiary)
        }
        .padding(.top, 2)
    }

    // MARK: - 草稿同步 (仅在列集合变化时重置, 不打断输入中的单元格)

    private func syncDrafts() {
        var newDrafts: [CellKey: String] = [:]
        var newDates: [String: String] = [:]
        for r in store.quarterlyReports {
            newDates[r.periodEnd] = dateDrafts[r.periodEnd] ?? r.periodEnd
            for f in QuarterlyField.allCases {
                let key = CellKey(periodEnd: r.periodEnd, field: f)
                newDrafts[key] = drafts[key] ?? formatDraft(r[keyPath: f.keyPath])
            }
        }
        drafts = newDrafts
        dateDrafts = newDates
    }

    /// 把已存数值转成紧凑可编辑文本：整数不带小数点，非整数用 `Double` 的最短往返表示。
    /// 旧实现用 `%g`（6 位有效数字），会把 123456.7 显示成 328980、234567.89 显示成 471416
    /// —— 与「填入精确值」(Q8=A) 冲突，这里修正为无损。
    private func formatDraft(_ v: Double?) -> String {
        guard let v else { return "" }
        if v.truncatingRemainder(dividingBy: 1) == 0, abs(v) < 1e15 { return String(format: "%.0f", v) }
        return String(v)
    }

    private func parseNumber(_ s: String) -> Double? {
        let t = s.trimmingCharacters(in: .whitespaces)
            .replacingOccurrences(of: ",", with: "")
            .replacingOccurrences(of: "−", with: "-")
        guard !t.isEmpty else { return nil }
        return Double(t)
    }

    // MARK: - 格式化

    private static let moneyFmt: NumberFormatter = {
        let f = NumberFormatter()
        f.numberStyle = .decimal
        f.minimumFractionDigits = 2
        f.maximumFractionDigits = 2
        f.usesGroupingSeparator = true
        return f
    }()

    private func money(_ v: Double?) -> String {
        guard let v else { return "" }
        return Self.moneyFmt.string(from: NSNumber(value: v)) ?? ""
    }

    private func pct(_ v: Double?) -> String {
        guard let v else { return "" }
        return String(format: "%.2f%%", v * 100)
    }

    private func signedPct(_ v: Double?) -> String {
        guard let v else { return "" }
        return String(format: "%+.2f%%", v * 100)
    }

    private func days(_ v: Int?) -> String {
        guard let v else { return "" }
        return String(v)
    }

    // MARK: - 行定义 (顺序与标签 1:1 对齐 xlsx)

    /// 网格的一行: 左侧标签 + 每季一单元格.
    private struct GridRow: Identifiable {
        let id: String
        let label: String
        let indent: Int
        let manual: QuarterlyField?
        let emphasis: Bool
        let isSection: Bool
        let value: (QuarterComputed) -> String

        static func manual(_ id: String, _ label: String, indent: Int = 0, _ field: QuarterlyField) -> GridRow {
            GridRow(id: id, label: label, indent: indent, manual: field, emphasis: false, isSection: false, value: { _ in "" })
        }

        static func computed(_ id: String, _ label: String, indent: Int = 0,
                              emphasis: Bool = false,
                              _ value: @escaping (QuarterComputed) -> String) -> GridRow {
            GridRow(id: id, label: label, indent: indent, manual: nil, emphasis: emphasis, isSection: false, value: value)
        }

        static func section(_ label: String) -> GridRow {
            GridRow(id: "section-\(label)", label: label, indent: 0, manual: nil, emphasis: false, isSection: true, value: { _ in "" })
        }
    }

    /// 全部行, 顺序/标签与 portfolio.xlsx 一致.
    private static var _rows: [GridRow] = [
        .section("资产"),
        .manual("mv", "总市值", .marketValue),
        .manual("cost", "总成本", indent: 1, .totalCost),
        .computed("principal", "本金", indent: 2) { m($0.principal) },
        .computed("cumRealized", "累计已实现回报", indent: 2) { money0($0.cumRealizedReturn) },
        .computed("cumInterest", "累计利息", indent: 3) { money0($0.cumInterest) },
        .computed("cumDividend", "累计股息", indent: 3) { money0($0.cumDividend) },
        .computed("cumGain", "累计已实现资本利得", indent: 3) { money0($0.cumRealizedGain) },
        .computed("unrealized", "未实现资本利得", indent: 1) { m($0.unrealizedGain) },
        .computed("cumNet", "累计净总回报", emphasis: true) { money0($0.cumNetReturn) },
        .computed("cumNetRate", "累计净总回报率") { p($0.cumNetReturnRate) },
        .computed("periodDays", "报告期") { d($0.periodDays) },

        .section("现金流"),
        .computed("interest", "利息") { m($0.interest) },
        .computed("interestYoY", "YoY%", indent: 1) { sp($0.interestYoY) },
        .manual("interestDom", "境内", indent: 1, .interestDomestic),
        .manual("interestOvs", "境外", indent: 1, .interestOverseas),
        .computed("dividend", "股息") { m($0.dividend) },
        .computed("dividendYoY", "YoY%", indent: 1) { sp($0.dividendYoY) },
        .manual("dividendDom", "境内", indent: 1, .dividendDomestic),
        .manual("dividendOvs", "境外", indent: 1, .dividendOverseas),
        .computed("capitalGain", "资本利得") { m($0.capitalGain) },
        .computed("capitalGainYoY", "YoY%", indent: 1) { sp($0.capitalGainYoY) },
        .manual("gainDom", "境内", indent: 1, .capitalGainDomestic),
        .manual("gainOvs", "境外", indent: 1, .capitalGainOverseas),
        .manual("taxes", "(红利税、资本利得税)", .taxes),
        .computed("newInvest", "新投资") { m($0.newInvestment) },
        .computed("primaryInvest", "一次投资", indent: 1) { m($0.primaryInvestment) },
        .computed("secondaryInvest", "二次投资", indent: 1) { m($0.secondaryInvestment) },
        .computed("secondaryPct", "%", indent: 1) { p($0.secondaryShare) },
        .computed("netReturn", "净总回报", emphasis: true) {
            ($0.index == 0 && $0.quarterNetReturn == 0) ? "" : money0($0.quarterNetReturn)
        },
        .computed("netReturnYoY", "YoY%", indent: 1) { sp($0.quarterNetReturnYoY) },
        .computed("incomeShare", "(股息+利息)/净总回报", indent: 1) { p($0.incomeShare) },
        .computed("quarterRate", "%") { p($0.quarterReturnRate) },
        .computed("annualized", "年化 %") { p($0.annualizedRate) },
        .computed("mean", "均值", indent: 1) { p($0.meanRate) },
        .computed("stddev", "标准差", indent: 1) { p($0.stdDev) },
        .computed("ciPlus", "+ 95% CI", indent: 1) { p($0.ciPlus) },
        .computed("ciMinus", "- 95% CI", indent: 1) { p($0.ciMinus) },
    ]

    private var rows: [GridRow] { Self._rows }

    // 行定义里用的轻量格式化助手 (static 上下文).
    private static func m(_ v: Double?) -> String {
        guard let v else { return "" }
        return moneyFmt.string(from: NSNumber(value: v)) ?? ""
    }
    private static func money0(_ v: Double) -> String { m(v) }
    private static func p(_ v: Double?) -> String {
        guard let v else { return "" }
        return String(format: "%.2f%%", v * 100)
    }
    private static func sp(_ v: Double?) -> String {
        guard let v else { return "" }
        return String(format: "%+.2f%%", v * 100)
    }
    private static func d(_ v: Int?) -> String {
        guard let v else { return "" }
        return String(v)
    }
}

/// 网格单元格坐标: 季度列 × 手动字段.
private struct CellKey: Hashable {
    let periodEnd: String
    let field: QuarterlyField
}
