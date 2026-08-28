import SwiftUI
import Charts
import PortfolioCore

/// 能力2：优化结果展示（权重 + 收益/波动/夏普 + 基准对比）。
/// 两种形态复用同一视图：
/// - 优化结果（默认）：标题右侧提供 [新标的测试] 与 [敏感性分析] 入口；
/// - 测试结果（isTestResult）：标题为「测试结果」，按要求不显示敏感性分析模块，
///   额外展示测试标的的估算参数；无 [新标的测试] 按钮（换标的须退回优化结果）。
public struct OptimizationResultView: View {
    let result: OptimizationResult
    var store: AppStore? = nil
    var isTestResult: Bool = false
    @Environment(\.dismiss) private var dismiss

    // Sensitivity analysis state
    @State private var showSensitivity = false
    @State private var sensitivityResult: SensitivityAnalysisResult?
    @State private var sensitivityLoading = false
    @State private var sensitivityError: String?

    // 新标的测试 state
    @State private var showTestInput = false

    public var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 10) {
                MacCloseButton { dismiss() }
                Text(isTestResult ? "测试结果" : "优化结果").font(.title2).fontWeight(.semibold)
                if store != nil && !isTestResult {
                    Button {
                        showTestInput = true
                    } label: {
                        Label("新标的测试", systemImage: "plus.magnifyingglass")
                    }
                    .buttonStyle(.bordered)
                    .disabled(store?.isTestOptimizing ?? false)
                }
                if !isTestResult {
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
                }
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

            // 测试结果形态: 展示测试标的的估算参数
            if isTestResult, let ta = result.testAssets, !ta.isEmpty {
                Divider()
                VStack(alignment: .leading, spacing: 6) {
                    Text("测试标的估算参数（μ = 100% 历史年化）").font(.headline)
                    ForEach(ta, id: \.key) { e in
                        HStack(spacing: 8) {
                            Text(e.ticker).fontWeight(.medium)
                            Text("\(e.source == "eastmoney" ? "天天基金" : "Yahoo") · \(e.pool == "domestic" ? "境内池" : "境外池") · \(e.nDays) 天")
                                .font(.caption).foregroundStyle(.secondary)
                            Spacer()
                            Text("μ \(pct(e.mu))   σ \(pct(e.vol))")
                                .font(.caption).monospacedDigit().foregroundStyle(.secondary)
                        }
                    }
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
        .sheet(isPresented: $showTestInput) {
            if let store {
                NewTickerTestInputView(store: store)
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

/// 新标的测试输入弹窗：逗号分隔临时标的 → AppStore.runTestOptimization。
/// 运行完成且成功时, 在本弹窗之上再弹出「测试结果」(store: nil 形态, 无重测入口)。
private struct NewTickerTestInputView: View {
    let store: AppStore
    @Environment(\.dismiss) private var dismiss
    @State private var input = ""
    @State private var showResult = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 8) {
                MacCloseButton { dismiss() }
                Text("新标的测试").font(.title3).fontWeight(.semibold)
                Spacer()
            }
            Text("输入一个或多个标的代码（英文逗号分隔），临时加入组合再跑一次优化，结果在「测试结果」弹窗中查看。原始「优化结果」不受影响。")
                .font(.callout)
            TextField("例如：NFLX, NVDA, 0700.HK, 510300", text: $input, axis: .vertical)
                .textFieldStyle(.roundedBorder)
                .lineLimit(2...3)
            Text("参数口径：预期收益 = 100% 历史年化；波动 = 历史年化波动；相关性 = 与各类资产代理指数的历史相关。6 位纯数字按境内基金（天天基金净值）处理并参与境内池约束；其余走 Yahoo 代码（美股 / 港股 0700.HK / 日股 7203.T / A股 600519.SS）。已在优化池内的标的会被跳过。")
                .font(.caption).foregroundStyle(.secondary)
            if store.isTestOptimizing {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text(store.testSteps.last.map { "[\($0.step)] \($0.message)" } ?? "正在启动测试优化…")
                        .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    Text("第 \(store.testSteps.count) 步").font(.caption2).foregroundStyle(.secondary)
                }
            }
            if let err = store.testOptimizeError, !store.isTestOptimizing {
                Text(err).font(.caption).foregroundStyle(.red)
            }
            HStack {
                Spacer()
                Button("取消") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("运行测试") {
                    store.runTestOptimization(tickers: parseInput(input))
                }
                .keyboardShortcut(.defaultAction)
                .disabled(parseInput(input).isEmpty || store.isTestOptimizing)
            }
        }
        .padding()
        .frame(width: 480)
        .sheet(isPresented: $showResult) {
            if let r = store.testOptimization {
                OptimizationResultView(result: r, store: nil, isTestResult: true)
            }
        }
        .onChange(of: store.isTestOptimizing) { old, new in
            if old && !new, store.testOptimization != nil, store.testOptimizeError == nil {
                showResult = true
            }
        }
    }

    private func parseInput(_ s: String) -> [String] {
        s.split(whereSeparator: { ",，;； \n\t".contains($0) })
            .map { $0.uppercased() }
            .filter { !$0.isEmpty }
    }
}
