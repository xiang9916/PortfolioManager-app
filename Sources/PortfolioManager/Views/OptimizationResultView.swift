import SwiftUI
import Charts
import PortfolioCore

/// 能力2：优化结果展示（权重 + 收益/波动/夏普 + 基准对比）。
public struct OptimizationResultView: View {
    let result: OptimizationResult
    @Environment(\.dismiss) private var dismiss

    // Sensitivity analysis state
    @State private var showSensitivity = false
    @State private var sensitivityResult: SensitivityAnalysisResult?
    @State private var sensitivityLoading = false
    @State private var sensitivityError: String?

    public var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 10) {
                MacCloseButton { dismiss() }
                Text("优化结果").font(.title2).fontWeight(.semibold)
                Button {
                    Task { await runSensitivity() }
                } label: {
                    if sensitivityLoading {
                        HStack(spacing: 6) {
                            ProgressView().controlSize(.small)
                            Text("敏感性分析")
                        }
                    } else {
                        Label("敏感性分析", systemImage: "chart.line.uptrend.xyaxis")
                    }
                }
                .buttonStyle(.bordered)
                .disabled(sensitivityLoading)
                Spacer()
            }

            // Summary
            HStack(spacing: 12) {
                statCard("预期收益", pct(result.portfolio.expectedReturn))
                statCard("波动率", pct(result.portfolio.volatility))
                statCard("夏普比率", String(format: "%.3f", result.portfolio.sharpe))
                statCard("95%最差年度", pct(result.portfolio.worstYear95))
            }

            // Weights bar chart
            Text("资产权重").font(.headline)
            Chart(result.assets.sorted(by: { $0.weight > $1.weight }), id: \.key) { a in
                BarMark(
                    x: .value("权重", a.weight),
                    y: .value("标的", a.name)
                )
                .foregroundStyle(.blue.gradient)
                .annotation(position: .trailing) {
                    Text(pct(a.weight)).font(.caption2).monospacedDigit()
                }
            }
            .frame(height: max(220, CGFloat(result.assets.count) * 28))

            if let b = result.benchmark {
                Divider()
                Text("基准对比（沪深300 + 标普500）").font(.headline)
                HStack(spacing: 12) {
                    statCard("组合 Alpha", pct(b.portfolioAlpha))
                    statCard("组合 Beta", String(format: "%.2f", b.portfolioBeta))
                    statCard("波动降低", pct(b.volatilityReduction))
                    statCard("最差年度改善", pct(b.worstYearImprovement))
                }
            }
        }
        .padding()
        .frame(minWidth: 640, minHeight: 460)
        .sheet(isPresented: $showSensitivity) {
            if let sr = sensitivityResult {
                SensitivityAnalysisView(result: sr)
            } else if let err = sensitivityError {
                VStack(spacing: 16) {
                    Text("敏感性分析失败").font(.headline)
                    Text(err).font(.caption).foregroundStyle(.secondary)
                    Button("关闭") { showSensitivity = false }
                }
                .padding()
            }
        }
    }

    private func runSensitivity() async {
        sensitivityLoading = true
        sensitivityError = nil
        sensitivityResult = nil
        do {
            // Get extract live path
            let extractURL = AppPaths.extractJSONURL()
                .deletingLastPathComponent()
                .appendingPathComponent("extract_live.json")
            guard FileManager.default.fileExists(atPath: extractURL.path) else {
                sensitivityError = "未找到提取文件：\(extractURL.path)。请先运行优化。"
                showSensitivity = true
                sensitivityLoading = false
                return
            }

            // Build sidecar + service
            // 敏感性分析复用优化运行留在共享临时目录的联网产物
            // (基金搜索易实时列表 + 天天基金实时申赎状态), 不再读预存快照。
            let sidecar = PythonSidecar(
                interpreterPath: AppPaths.interpreterPath(),
                scriptsDir: AppPaths.scriptsURL(),
                currentDirectoryURL: URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
            )
            let logsDir = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
                .appendingPathComponent("tmp/optimizer_logs", isDirectory: true)
            let tempDB = try Database(path: AppPaths.databaseURL().path)
            let optimizer = OptimizationService(db: tempDB, sidecar: sidecar, logsDir: logsDir)

            let sr = try optimizer.runSensitivityAnalysis(extractJSON: extractURL)
            sensitivityResult = sr
            showSensitivity = true
        } catch {
            sensitivityError = String(describing: error)
            showSensitivity = true
        }
        sensitivityLoading = false
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
    private func pct(_ v: Double) -> String { String(format: "%.2f%%", v * 100) }
}
