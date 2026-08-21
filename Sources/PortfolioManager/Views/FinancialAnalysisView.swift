import SwiftUI
import Charts
import PortfolioCore

/// 能力4：财务分析 — 分析个人资产/收益结构 (像公司财务底稿一样).
public struct FinancialAnalysisView: View {
    @Bindable var store: AppStore
    @State private var showAdd = false
    @State private var showManage = false

    public var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                if let fa = store.financialAnalysis {
                    assetStructure(fa)
                    realizedPnlStructure(fa)
                    incomeStructure(fa)
                }
                incomePeriodsSection()
            }
            .padding()
        }
        .navigationTitle("财务分析")
        .toolbar {
            ToolbarItemGroup {
                Button { showAdd = true } label: { Label("添加期间", systemImage: "plus") }
                Button { showManage = true } label: { Label("管理", systemImage: "slider.horizontal.3") }
            }
        }
        .sheet(isPresented: $showAdd) { IncomeSummaryEditorSheet(store: store) }
        .sheet(isPresented: $showManage) { IncomeSummaryManageSheet(store: store) }
    }

    // MARK: 资产结构 (本金 vs 市值)

    private func assetStructure(_ fa: FinancialAnalysis) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("资产结构").font(.headline)
            // 第一行: 原始本金、实盈实亏、收益率 (收益率 = 实盈实亏 / 原始本金)
            HStack(spacing: 16) {
                statCard("原始本金", money(fa.originalPrincipal), "banknote", .teal)
                statCard("实盈实亏", signedMoney(fa.realizedPnl), "arrow.left.arrow.right", fa.realizedPnl >= 0 ? .orange : .red)
                statCard("收益率", pct(fa.returnRate), "percent", fa.returnRate >= 0 ? .green : .red)
            }
            // 第二行: 本金、浮盈浮亏、市值
            HStack(spacing: 16) {
                statCard("本金", money(fa.principal), "banknote.fill", .teal)
                statCard("浮盈浮亏", signedMoney(fa.unrealizedPnl), "arrow.up.right", fa.unrealizedPnl >= 0 ? .green : .red)
                statCard("市值", money(fa.marketValue), "chart.line.uptrend.xyaxis", .blue)
            }
            // 三段恒等式条: 市值 = 原始本金 + 实盈实亏 + 浮盈浮亏
            if fa.marketValue != 0 {
                let denom = abs(fa.originalPrincipal) + abs(fa.realizedPnl) + abs(fa.unrealizedPnl)
                let p1 = denom > 0 ? abs(fa.originalPrincipal) / denom : 0
                let p2 = denom > 0 ? abs(fa.realizedPnl) / denom : 0
                let p3 = denom > 0 ? abs(fa.unrealizedPnl) / denom : 0
                VStack(alignment: .leading, spacing: 4) {
                    GeometryReader { geo in
                        HStack(spacing: 0) {
                            Rectangle().fill(.teal).frame(width: geo.size.width * p1)
                            Rectangle().fill(.orange).frame(width: geo.size.width * p2)
                            Rectangle().fill(fa.unrealizedPnl >= 0 ? Color.green : Color.red).frame(width: geo.size.width * p3)
                        }
                    }
                    .frame(height: 12)
                    .clipShape(RoundedRectangle(cornerRadius: 6))
                    HStack {
                        Text("原始本金 " + pct(p1)).foregroundStyle(.teal)
                        Text("实盈实亏 " + pct(p2)).foregroundStyle(.orange)
                        Spacer()
                        Text("浮盈浮亏 " + pct(p3)).foregroundStyle(fa.unrealizedPnl >= 0 ? .green : .red)
                    }.font(.caption)
                    Text("市值 = 原始本金 + 实盈实亏 + 浮盈浮亏").font(.caption2).foregroundStyle(.secondary)
                }
            }
        }
        .padding()
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 12))
    }

    // MARK: 实盈实亏 (累计股息分红 + 累计交易损益)

    private func realizedPnlStructure(_ fa: FinancialAnalysis) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("实盈实亏（累计至今）").font(.headline)
            HStack(spacing: 16) {
                statCard("累计股息分红", money(fa.totalDividends), "banknote", .orange)
                statCard("累计交易损益", signedMoney(fa.totalRealizedPnl), "arrow.left.arrow.right", fa.totalRealizedPnl >= 0 ? .green : .red)
                statCard("实盈实亏合计", signedMoney(fa.realizedPnl), "sum", fa.realizedPnl >= 0 ? .green : .red)
            }
            Text("本金 = 原始本金 + 实盈实亏").font(.caption).foregroundStyle(.secondary)
        }
        .padding()
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 12))
    }

    // MARK: 收益结构 (浮盈 / 股息 / 交易损益)

    private func incomeStructure(_ fa: FinancialAnalysis) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("收益结构（浮盈浮亏 · 股息分红 · 交易损益）").font(.headline)
            HStack(spacing: 16) {
                statCard("浮盈浮亏", signedMoney(fa.unrealizedPnl), "chart.line.uptrend.xyaxis", fa.unrealizedPnl >= 0 ? .green : .red)
                statCard("股息分红", money(fa.totalDividends), "banknote", .orange)
                statCard("交易损益", signedMoney(fa.totalRealizedPnl), "arrow.left.arrow.right", fa.totalRealizedPnl >= 0 ? .green : .red)
                statCard("合计收益", signedMoney(fa.totalIncome), "sum", fa.totalIncome >= 0 ? .green : .red)
            }
            HStack {
                Text("合计收益率").foregroundStyle(.secondary)
                Spacer()
                Text(pct(fa.totalReturnRate)).font(.title3).fontWeight(.semibold).monospacedDigit()
                    .foregroundStyle(fa.totalReturnRate >= 0 ? .green : .red)
            }
        }
        .padding()
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 12))
    }

    // MARK: 期间明细 (季度录入 → 半年/年度 派生)

    private func incomePeriodsSection() -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("收益期间明细").font(.headline)
            if store.incomeSummaries.isEmpty {
                ContentUnavailableView("暂无收益期间", systemImage: "chart.bar.doc.horizontal",
                    description: Text("点右上角「添加期间」按季度录入；半年/年度自动汇总。"))
            } else {
                periodTable()
            }
        }
        .padding()
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 12))
    }

    /// Derived half-year or annual summary from quarterly records.
    private struct DerivedPeriod: Identifiable {
        let label: String   // e.g. "2026 H1" or "2026"
        let dividends: Double
        let realizedPnl: Double
        var id: String { label }
    }

    /// Group quarterly records by year and half (H1=Q1+Q2, H2=Q3+Q4).
    private func derivedHalfYearSummaries(_ quarters: [IncomeSummary]) -> [DerivedPeriod] {
        // Extract year from periodEnd (first 4 chars).
        var byYearHalf: [String: (div: Double, pnl: Double)] = [:]
        for q in quarters {
            let yr = String(q.periodEnd.prefix(4))
            guard let mm = Int(q.periodEnd.dropFirst(5).prefix(2)) else { continue }
            let half = mm <= 6 ? "H1" : "H2"
            let key = "\(yr) \(half)"
            let cur = byYearHalf[key] ?? (0, 0)
            byYearHalf[key] = (cur.div + q.dividends, cur.pnl + q.realizedPnl)
        }
        return byYearHalf.keys.sorted().map { k in
            let v = byYearHalf[k]!
            return DerivedPeriod(label: k, dividends: v.div, realizedPnl: v.pnl)
        }.sorted { $0.label > $1.label }
    }

    /// Group quarterly records by year.
    private func derivedAnnualSummaries(_ quarters: [IncomeSummary]) -> [DerivedPeriod] {
        var byYear: [String: (div: Double, pnl: Double)] = [:]
        for q in quarters {
            let yr = String(q.periodEnd.prefix(4))
            let cur = byYear[yr] ?? (0, 0)
            byYear[yr] = (cur.div + q.dividends, cur.pnl + q.realizedPnl)
        }
        return byYear.keys.sorted().map { k in
            let v = byYear[k]!
            return DerivedPeriod(label: k, dividends: v.div, realizedPnl: v.pnl)
        }.sorted { $0.label > $1.label }
    }

    private func periodTable() -> some View {
        let all = store.incomeSummaries.sorted { $0.periodEnd > $1.periodEnd }
        let quarters = all.filter { $0.period == .quarter }
        let nonQuarter = all.filter { $0.period != .quarter }  // legacy rows
        let halfYears = derivedHalfYearSummaries(quarters)
        let annuals = derivedAnnualSummaries(quarters)
        return VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 0) {
                headerCell("期间 / 口径", width: 200)
                headerCell("股息分红 (¥)", width: 160)
                headerCell("交易损益 (¥)", width: 160)
                headerCell("期间收益 (¥)", width: 160)
            }
            Divider()
            // Section: Quarterly (raw)
            sectionHeader("季度（录入）")
            ForEach(quarters) { f in
                HStack(spacing: 0) {
                    cell(f.periodEnd + " · 季度", width: 200, bold: true)
                    cell(money(f.dividends), width: 160, bold: false)
                    cell(signedMoney(f.realizedPnl), width: 160, bold: false)
                    cell(signedMoney(f.dividends + f.realizedPnl), width: 160, bold: false)
                }
                Divider()
            }
            // Section: Legacy non-quarter (if any old rows exist)
            if !nonQuarter.isEmpty {
                sectionHeader("历史记录（半年/年度）")
                ForEach(nonQuarter) { f in
                    HStack(spacing: 0) {
                        cell(f.periodEnd + " · " + periodName(f.period), width: 200, bold: true)
                        cell(money(f.dividends), width: 160, bold: false)
                        cell(signedMoney(f.realizedPnl), width: 160, bold: false)
                        cell(signedMoney(f.dividends + f.realizedPnl), width: 160, bold: false)
                    }
                    Divider()
                }
            }
            // Section: Half-year (derived)
            if !halfYears.isEmpty {
                sectionHeader("半年（派生）")
                ForEach(halfYears) { h in
                    HStack(spacing: 0) {
                        cell(h.label + " · 半年", width: 200, bold: false)
                        cell(money(h.dividends), width: 160, bold: false)
                        cell(signedMoney(h.realizedPnl), width: 160, bold: false)
                        cell(signedMoney(h.dividends + h.realizedPnl), width: 160, bold: false)
                    }
                    Divider()
                }
            }
            // Section: Annual (derived)
            if !annuals.isEmpty {
                sectionHeader("年度（派生）")
                ForEach(annuals) { a in
                    HStack(spacing: 0) {
                        cell(a.label + " · 年度", width: 200, bold: false)
                        cell(money(a.dividends), width: 160, bold: false)
                        cell(signedMoney(a.realizedPnl), width: 160, bold: false)
                        cell(signedMoney(a.dividends + a.realizedPnl), width: 160, bold: false)
                    }
                    Divider()
                }
            }
        }
    }

    private func sectionHeader(_ title: String) -> some View {
        Text(title).font(.caption).fontWeight(.semibold).foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, alignment: .leading).padding(8)
            .background(Color.gray.opacity(0.08))
    }

    private func headerCell(_ text: String, width: CGFloat) -> some View {
        Text(text).font(.caption).fontWeight(.semibold).foregroundStyle(.secondary)
            .frame(width: width, alignment: .leading).padding(8)
    }
    private func cell(_ text: String, width: CGFloat, bold: Bool) -> some View {
        Text(text).font(bold ? .subheadline.bold() : .subheadline).monospacedDigit()
            .frame(width: width, alignment: .leading).padding(8)
    }

    private func statCard(_ label: String, _ value: String, _ icon: String, _ color: Color) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Label(label, systemImage: icon).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.title2).fontWeight(.semibold).monospacedDigit().foregroundStyle(color)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
        .background(color.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
    }

    private func periodName(_ p: FinancialPeriod) -> String {
        switch p {
        case .quarter: return "季度"
        case .halfYear: return "半年"
        case .annual: return "年度"
        }
    }
    private func money(_ v: Double) -> String {
        let f = NumberFormatter(); f.numberStyle = .decimal; f.maximumFractionDigits = 0
        return f.string(from: NSNumber(value: v)) ?? "0"
    }
    private func signedMoney(_ v: Double) -> String {
        (v >= 0 ? "+" : "-") + money(abs(v))
    }
    private func pct(_ v: Double) -> String { String(format: "%.2f%%", v * 100) }
}

/// 添加/编辑一个收益期间汇总 (能力4 手动录入).
public struct IncomeSummaryEditorSheet: View {
    @Bindable var store: AppStore
    @Environment(\.dismiss) private var dismiss
    let existing: IncomeSummary?

    @State private var period: FinancialPeriod
    @State private var periodEnd: String
    @State private var dividends: String
    @State private var realizedPnl: String

    public init(store: AppStore, existing: IncomeSummary? = nil) {
        self.store = store
        self.existing = existing
        _period = State(initialValue: existing?.period ?? .quarter)
        _periodEnd = State(initialValue: existing?.periodEnd ?? "")
        _dividends = State(initialValue: existing.map { String(format: "%g", $0.dividends) } ?? "")
        _realizedPnl = State(initialValue: existing.map { String(format: "%g", $0.realizedPnl) } ?? "")
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("收益期间（季度）").font(.headline)
            Text("仅录入季度数据；半年度/年度由季度汇总派生。").font(.caption).foregroundStyle(.secondary)
            TextField("季度截止日（如 2026-03-31 / 2026-06-30 / 2026-09-30 / 2026-12-31）", text: $periodEnd)
                .textFieldStyle(.roundedBorder)
            HStack(spacing: 10) {
                numField("股息分红 (¥)", $dividends)
                numField("交易损益 (¥)", $realizedPnl)
            }
            HStack {
                Spacer()
                Button("取消") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("保存") { save() }.keyboardShortcut(.defaultAction).buttonStyle(.borderedProminent)
            }
        }
        .padding()
        .frame(width: 460)
    }

    private func numField(_ label: String, _ text: Binding<String>) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(.caption).foregroundStyle(.secondary)
            TextField(label, text: text).textFieldStyle(.roundedBorder)
        }
    }

    private func save() {
        let f = IncomeSummary(period: period, periodEnd: periodEnd,
                             dividends: Double(dividends) ?? 0, realizedPnl: Double(realizedPnl) ?? 0,
                             source: "manual")
        store.upsertIncomeSummary(f)
        dismiss()
    }
}

/// 管理 (查看/编辑/删除) 所有收益期间记录.
public struct IncomeSummaryManageSheet: View {
    @Bindable var store: AppStore
    @Environment(\.dismiss) private var dismiss
    @State private var editing: IncomeSummary?

    public var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("收益期间管理").font(.headline)
            if store.incomeSummaries.isEmpty {
                ContentUnavailableView("暂无记录", systemImage: "chart.bar.doc.horizontal",
                    description: Text("点右上角「添加期间」录入。"))
            } else {
                List {
                    ForEach(store.incomeSummaries.sorted { $0.periodEnd > $1.periodEnd }) { f in
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(periodName(f.period) + " · " + f.periodEnd)
                                    .font(.subheadline).fontWeight(.medium)
                                Text("股息 " + money(f.dividends) + " · 交易损益 " + signedMoney(f.realizedPnl))
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Button { editing = f } label: { Image(systemName: "pencil") }
                                .buttonStyle(.borderless).help("编辑")
                            Button(role: .destructive) { store.deleteIncomeSummary(id: f.id ?? 0) } label: {
                                Image(systemName: "trash")
                            }.buttonStyle(.borderless).help("删除")
                        }
                        .padding(.vertical, 2)
                    }
                }
            }
            HStack {
                Spacer()
                Button("关闭") { dismiss() }.keyboardShortcut(.cancelAction)
            }
        }
        .padding()
        .frame(width: 520, height: 420)
        .sheet(item: $editing) { f in
            IncomeSummaryEditorSheet(store: store, existing: f)
        }
    }

    private func periodName(_ p: FinancialPeriod) -> String {
        switch p {
        case .quarter: return "季度"
        case .halfYear: return "半年"
        case .annual: return "年度"
        }
    }
    private func money(_ v: Double) -> String {
        let f = NumberFormatter(); f.numberStyle = .decimal; f.maximumFractionDigits = 0
        return f.string(from: NSNumber(value: v)) ?? "0"
    }
    private func signedMoney(_ v: Double) -> String {
        (v >= 0 ? "+" : "-") + money(abs(v))
    }
}
