import SwiftUI
import PortfolioCore

/// 能力4：季度/半年/年度财务报表对比视图（类似炒股软件的财报对比）。
public struct FinancialComparisonView: View {
    @Bindable var store: AppStore
    @State private var period: FinancialPeriod = .quarter
    @State private var showAdd = false
    @State private var showManage = false

    public var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Picker("报告期", selection: $period) {
                Text("季度").tag(FinancialPeriod.quarter)
                Text("半年").tag(FinancialPeriod.halfYear)
                Text("年度").tag(FinancialPeriod.annual)
            }
            .pickerStyle(.segmented)
            .frame(maxWidth: 360)

            let filtered = store.financials.filter { $0.period == period }
            if filtered.isEmpty {
                ContentUnavailableView(
                    "暂无财务报表数据",
                    systemImage: "chart.bar.doc.horizontal",
                    description: Text("抓取财务报表数据（能力1 扩展）后，将在此按季度/半年/年度对比展示营收、净利润、EPS。")
                )
            } else {
                comparisonTable(filtered)
            }
        }
        .padding()
        .navigationTitle("财务报表对比")
        .toolbar {
            ToolbarItemGroup {
                Button { showAdd = true } label: { Label("添加报表", systemImage: "plus") }
                Button { showManage = true } label: { Label("管理", systemImage: "slider.horizontal.3") }
            }
        }
        .sheet(isPresented: $showAdd) { FinancialEditorSheet(store: store) }
        .sheet(isPresented: $showManage) { FinancialManageSheet(store: store) }
    }

    private func comparisonTable(_ items: [Financial]) -> some View {
        let periods = orderedPeriods(items)
        let assets = orderedAssets(items)
        return ScrollView([.horizontal, .vertical]) {
            VStack(alignment: .leading, spacing: 0) {
                HStack(spacing: 0) {
                    headerCell("标的 / 报告期", width: 160)
                    ForEach(periods, id: \.self) { p in
                        headerCell(p, width: 200)
                    }
                }
                Divider()
                ForEach(assets, id: \.self) { key in
                    HStack(spacing: 0) {
                        cell(key, width: 160, bold: true)
                        ForEach(periods, id: \.self) { p in
                            cell(values(for: key, items: items)[p] ?? "—", width: 200, bold: false)
                        }
                    }
                    Divider()
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    private func headerCell(_ text: String, width: CGFloat) -> some View {
        Text(text)
            .font(.caption).fontWeight(.semibold).foregroundStyle(.secondary)
            .frame(width: width, alignment: .leading)
            .padding(8)
    }
    private func cell(_ text: String, width: CGFloat, bold: Bool) -> some View {
        Text(text)
            .font(bold ? .subheadline.bold() : .subheadline)
            .monospacedDigit()
            .frame(width: width, alignment: .leading)
            .padding(8)
    }

    private func orderedPeriods(_ items: [Financial]) -> [String] {
        Array(Set(items.map { $0.periodEnd })).sorted(by: >)
    }
    private func orderedAssets(_ items: [Financial]) -> [String] {
        var seen: [String] = []
        for f in items where !seen.contains(f.assetKey) { seen.append(f.assetKey) }
        return seen
    }
    private func values(for key: String, items: [Financial]) -> [String: String] {
        var out: [String: String] = [:]
        for f in items where f.assetKey == key {
            var parts: [String] = []
            if let r = f.revenue { parts.append("营收 " + fmt(r)) }
            if let n = f.netIncome { parts.append("净利 " + fmt(n)) }
            if let e = f.eps { parts.append("EPS " + String(format: "%.2f", e)) }
            out[f.periodEnd] = parts.isEmpty ? "—" : parts.joined(separator: "  ")
        }
        return out
    }
    private func fmt(_ v: Double) -> String {
        let f = NumberFormatter(); f.numberStyle = .decimal; f.maximumFractionDigits = 0
        return f.string(from: NSNumber(value: v)) ?? "0"
    }
}

/// 添加/编辑单条财务报表记录（能力4 手动录入）。
struct FinancialEditorSheet: View {
    @Bindable var store: AppStore
    @Environment(\.dismiss) private var dismiss
    let existing: Financial?

    @State private var assetKey: String
    @State private var period: FinancialPeriod
    @State private var periodEnd: String
    @State private var revenue: String
    @State private var netIncome: String
    @State private var eps: String

    init(store: AppStore, existing: Financial? = nil) {
        self.store = store
        self.existing = existing
        _assetKey = State(initialValue: existing?.assetKey ?? store.perspectives.first?.assetKey ?? "")
        _period = State(initialValue: existing?.period ?? .quarter)
        _periodEnd = State(initialValue: existing?.periodEnd ?? "")
        _revenue = State(initialValue: existing?.revenue.map { String(format: "%g", $0) } ?? "")
        _netIncome = State(initialValue: existing?.netIncome.map { String(format: "%g", $0) } ?? "")
        _eps = State(initialValue: existing?.eps.map { String(format: "%g", $0) } ?? "")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(existing == nil ? "添加财务报表" : "编辑财务报表").font(.headline)

            Picker("标的", selection: $assetKey) {
                ForEach(store.perspectives) { row in
                    Text(row.name).tag(row.assetKey)
                }
            }

            Picker("报告期", selection: $period) {
                Text("季度").tag(FinancialPeriod.quarter)
                Text("半年").tag(FinancialPeriod.halfYear)
                Text("年度").tag(FinancialPeriod.annual)
            }
            .pickerStyle(.segmented)

            TextField("报告期截止（如 2026-06-30 或 2026Q2）", text: $periodEnd)
                .textFieldStyle(.roundedBorder)

            HStack(spacing: 10) {
                numField("营收", $revenue)
                numField("净利润", $netIncome)
                numField("EPS", $eps)
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
        let f = Financial(assetKey: assetKey, period: period, periodEnd: periodEnd,
                          revenue: Double(revenue), netIncome: Double(netIncome),
                          eps: Double(eps), source: "manual")
        store.upsertFinancial(f)
        dismiss()
    }
}

/// 管理（查看/编辑/删除）所有财务报表记录。
struct FinancialManageSheet: View {
    @Bindable var store: AppStore
    @Environment(\.dismiss) private var dismiss
    @State private var editing: Financial?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("财务报表管理").font(.headline)
            if store.financials.isEmpty {
                ContentUnavailableView("暂无记录", systemImage: "chart.bar.doc.horizontal",
                                       description: Text("点右上角「添加报表」录入。"))
            } else {
                List {
                    ForEach(store.financials) { f in
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(f.assetKey).font(.subheadline).fontWeight(.medium)
                                Text(f.period.rawValue + " · " + f.periodEnd)
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Button { editing = f } label: { Image(systemName: "pencil") }
                                .buttonStyle(.borderless).help("编辑")
                            Button(role: .destructive) { store.deleteFinancial(id: f.id ?? 0) } label: {
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
        .frame(width: 480, height: 420)
        .sheet(item: $editing) { f in
            FinancialEditorSheet(store: store, existing: f)
        }
    }
}
