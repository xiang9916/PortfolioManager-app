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

                Button {
                    exportBackup()
                } label: {
                    Label("导出备份", systemImage: "square.and.arrow.up")
                }
                .help("导出备份数据 JSON（持仓/收益期间/汇率）")

                Button {
                    importBackup()
                } label: {
                    Label("导入备份", systemImage: "square.and.arrow.down")
                }
                .help("导入备份数据 JSON")
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
            if alloc.crossValue > 0 {
                metricCard("跨池", money(alloc.crossValue), "arrow.triangle.swap", .yellow)
            }
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
            Text("模拟历史财务表现（加权净值，近3年）").font(.headline)
                .help("口径：以当前持仓市值权重，将各标的累计净值在近3年窗口内归一为1.0后加权合成；不含汇率、不含历史调仓。基金用累计净值（含分红再投），股票用收盘价。基准=沪深300+标普500按当前境内/境外池占比加权（与优化器一致）。")
            let pts = store.performancePoints.chartPoints(series: "组合")
            let benchPts = store.benchmarkPoints.chartPoints(series: "基准")
            // 显式 y 域: 避免自动域撑到 0..3 导致顶部浮出无意义网格线.
            let allValues = (store.performancePoints + store.benchmarkPoints).map { $0.value }
            let lo = (allValues.min() ?? 1.0) - 0.03
            let hi = (allValues.max() ?? 1.0) + 0.03
            // 多系列必须用 foregroundStyle(by:) + chartForegroundStyleScale 区分系列:
            // 直接给第二个 ForEach 的 LineMark 设静态颜色会被 Charts 忽略,
            // 基准线会回落到默认调色板第一色(蓝) —— 勿改回静态前景色写法.
            Chart {
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

    private func exportBackup() {
        let panel = NSSavePanel()
        panel.allowedContentTypes = [UTType.json]
        panel.nameFieldStringValue = "PortfolioBackup.json"
        panel.begin { resp in
            guard resp == .OK, let url = panel.url else { return }
            do {
                try store.exportBackup(to: url)
                store.statusMessage = "已导出备份: \(url.lastPathComponent)"
            } catch {
                store.statusMessage = "导出备份失败: \(error)"
            }
        }
    }

    private func importBackup() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [UTType.json]
        panel.allowsMultipleSelection = false
        panel.begin { resp in
            guard resp == .OK, let url = panel.url else { return }
            Task {
                do {
                    try await store.importBackup(from: url)
                    store.statusMessage = "已导入备份: \(url.lastPathComponent)"
                } catch {
                    store.statusMessage = "导入备份失败: \(error)"
                }
            }
        }
    }
}
