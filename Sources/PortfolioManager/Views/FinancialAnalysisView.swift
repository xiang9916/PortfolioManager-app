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
            Text("资产结构（本金 vs 市值）").font(.headline)
            HStack(spacing: 16) {
                statCard("本金", money(fa.principal), "banknote", .teal)
                statCard("市值", money(fa.marketValue), "chart.line.uptrend.xyaxis", .blue)
                statCard("浮盈浮亏", signedMoney(fa.unrealizedPnl), "arrow.up.right", fa.unrealizedPnl >= 0 ? .green : .red)
                statCard("收益率", pct(fa.returnRate), "percent", fa.returnRate >= 0 ? .green : .red)
            }
            // 本金 vs 市值 比例条
            if fa.marketValue + fa.principal > 0 {
                let principalRatio = (fa.marketValue + fa.principal) > 0 ? fa.principal / (fa.marketValue + fa.principal) : 0
                VStack(alignment: .leading, spacing: 4) {
                    GeometryReader { geo in
                        HStack(spacing: 0) {
                            Rectangle().fill(.teal).frame(width: geo.size.width * principalRatio)
                            Rectangle().fill(.blue).frame(width: geo.size.width * (1 - principalRatio))
                        }
                    }
                    .frame(height: 12)
                    .clipShape(RoundedRectangle(cornerRadius: 6))
                    HStack {
                        Text("本金 " + pct(principalRatio)).foregroundStyle(.teal)
                        Spacer()
                        Text("市值 " + pct(1 - principalRatio)).foregroundStyle(.blue)
                    }.font(.caption)
                }
            }
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

    // MARK: 期间明细 (季度/半年/年度 收益结构)

    private func incomePeriodsSection() -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("收益期间明细").font(.headline)
            if store.incomeSummaries.isEmpty {
                ContentUnavailableView("暂无收益期间", systemImage: "chart.bar.doc.horizontal",
                    description: Text("点右上角「添加期间」，按季度/半年/年度记录股息分红与交易损益。"))
            } else {
                periodTable()
            }
        }
        .padding()
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 12))
    }

    private func periodTable() -> some View {
        let items = store.incomeSummaries.sorted { $0.periodEnd > $1.periodEnd }
        return VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 0) {
                headerCell("期间 / 口径", width: 200)
                headerCell("股息分红 (¥)", width: 160)
                headerCell("交易损益 (¥)", width: 160)
                headerCell("期间收益 (¥)", width: 160)
            }
            Divider()
            ForEach(items) { f in
                HStack(spacing: 0) {
                    cell(f.periodEnd + " · " + periodName(f.period), width: 200, bold: true)
                    cell(money(f.dividends), width: 160, bold: false)
                    cell(signedMoney(f.realizedPnl), width: 160, bold: false)
                    cell(signedMoney(f.dividends + f.realizedPnl), width: 160, bold: false)
                }
                Divider()
            }
        }
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
            Text("收益期间").font(.headline)
            Picker("口径", selection: $period) {
                Text("季度").tag(FinancialPeriod.quarter)
                Text("半年").tag(FinancialPeriod.halfYear)
                Text("年度").tag(FinancialPeriod.annual)
            }
            .pickerStyle(.segmented)
            TextField("期间截止（如 2026-06-30 / 2026Q2 / 2026）", text: $periodEnd)
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
