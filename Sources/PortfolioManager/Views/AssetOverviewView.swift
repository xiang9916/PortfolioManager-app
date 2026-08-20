import SwiftUI
import AppKit
import Charts
import UniformTypeIdentifiers
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
        .toolbar {
            ToolbarItemGroup {
                Button {
                    Task { await store.refreshPrices() }
                } label: {
                    Label("更新行情", systemImage: "arrow.clockwise")
                }
                .help("自动抓取公开行情数据（能力1）")

                Button {
                    exportPDF()
                } label: {
                    Label("导出 PDF", systemImage: "doc.richtext")
                }
                .help("导出投资组合报告（能力3）")
            }
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
        HStack(spacing: 16) {
            metricCard("总资产", money(alloc.totalValue), "chart.pie.fill", .blue)
            metricCard("境内", money(alloc.domesticValue), "house.fill", .teal)
            metricCard("境外", money(alloc.overseasValue), "globe", .indigo)
            if let s = store.performanceSummary {
                metricCard("近3年收益", pct(s.totalReturn), "chart.line.uptrend.xyaxis", s.totalReturn >= 0 ? .green : .red)
                metricCard("年化波动", pct(s.annualizedVolatility), "waveform.path.ecg", .orange)
                metricCard("最大回撤", pct(s.maxDrawdown), "arrow.down.right", .purple)
            }
        }
    }

    private func metricCard(_ title: String, _ value: String, _ icon: String, _ color: Color) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Label(title, systemImage: icon).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.title2).fontWeight(.semibold).monospacedDigit()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding()
        .background(color.opacity(0.12), in: RoundedRectangle(cornerRadius: 12))
    }

    // MARK: allocation

    private func allocationSection(_ alloc: AllocationSnapshot) -> some View {
        HStack(alignment: .top, spacing: 24) {
            // Donut
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
            Text("历史财务表现（加权净值，近3年）").font(.headline)
            let pts = store.performancePoints.chartPoints
            Chart(pts) { p in
                AreaMark(x: .value("日期", p.date), y: .value("净值", p.value))
                    .foregroundStyle(.blue.opacity(0.15))
                LineMark(x: .value("日期", p.date), y: .value("净值", p.value))
                    .foregroundStyle(.blue)
                    .lineStyle(StrokeStyle(lineWidth: 1.5))
            }
            .frame(height: 260)
        }
        .padding()
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 12))
    }

    // MARK: helpers

    private func money(_ v: Double) -> String {
        let f = NumberFormatter()
        f.numberStyle = .decimal
        f.maximumFractionDigits = 0
        return "¥" + (f.string(from: NSNumber(value: v)) ?? "0")
    }
    private func pct(_ v: Double) -> String {
        String(format: "%.2f%%", v * 100)
    }

    private func exportPDF() {
        let panel = NSSavePanel()
        panel.allowedContentTypes = [UTType.pdf]
        panel.nameFieldStringValue = "PortfolioReport.pdf"
        panel.begin { resp in
            guard resp == .OK, let url = panel.url else { return }
            do {
                try store.exportPDF(to: url)
                store.statusMessage = "已导出: \(url.lastPathComponent)"
            } catch {
                store.statusMessage = "导出失败: \(error)"
            }
        }
    }
}
