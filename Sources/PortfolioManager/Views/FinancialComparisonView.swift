import SwiftUI
import PortfolioCore

/// 能力4：季度/半年/年度财务报表对比视图（类似炒股软件的财报对比）。
public struct FinancialComparisonView: View {
    @Bindable var store: AppStore
    @State private var period: FinancialPeriod = .quarter

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
