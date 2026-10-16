import SwiftUI
import AppKit
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

    // 长列表可折叠（弹窗尺寸固定 + 内部滚动，折叠后不用滚）
    @State private var showTestAssets = true
    @State private var showWarnings = true

    /// 顶部固定栏（标题 + 入口按钮）：正文很长时它不参与滚动，关闭按钮始终可见
    private var headerRow: some View {
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
    }

    /// 弹窗尺寸：内容长度不可控，此前只给 minHeight，sheet 会按内容一路长到屏幕之外。
    /// 现在固定成「屏幕可见高度 − 余量」，超出部分在内部滚动。
    private var panelSize: CGSize {
        let visible = NSScreen.main?.visibleFrame.height ?? 900
        return CGSize(width: 900, height: max(420, min(800, visible - 150)))
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            headerRow
            // 测试结果页此前把"回退/不可行"类提示埋在可折叠的提示区里，用户看不到任何异常信号。
            // 语义上属于「目标未达成 / 交付受限」的条目提到固定区（不随正文滚动移出视野）。
            if isTestResult, !criticalWarnings.isEmpty {
                criticalWarningBanner
            }
            // 正文长度不可控（标的数、提示条数都能把它撑长）→ 固定尺寸 + 内部滚动
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    // Summary
                    HStack(spacing: 12) {
                        statCard("预期收益", pct(result.portfolio.expectedReturn))
                        statCard("波动率", pct(result.portfolio.volatility))
                        statCard("夏普比率", String(format: "%.3f", result.portfolio.sharpe))
                        statCard("95%最差年度", pct(result.portfolio.worstYear95))
                    }

                    // 测试结果页此前**完全没有**这一行（只有主结果页有）：交付低于目标时用户
                    // 只能看到"收益下降、波动上升"，没有任何异常信号。补齐「实际 − 目标」
                    // （复用 result.targetReturn / targetGap，未改公开接口）。
                    if isTestResult, let target = result.targetReturn {
                        testTargetGapRow(target: target)
                    }

                    // 目标收益率只是下限：优化目标是最大化夏普，实际收益可以高于目标。
                    // （主结果页：文案与阈值保持不变，本轮不动它）
                    if let target = result.targetReturn, !isTestResult {
                        let gap = result.portfolio.expectedReturn - target
                        HStack(spacing: 6) {
                            Image(systemName: gap > 0.002 ? "info.circle" : "checkmark.circle")
                                .foregroundStyle(gap > 0.002 ? Color.orange : Color.secondary)
                            Text(gap > 0.002
                                 ? "目标收益率 \(pct(target)) 是收益**下限**（不是锚）：求解目标是最大化夏普，实际交付 \(pct(result.portfolio.expectedReturn))，高出 \(pct(gap))。目标低于切点收益时结果不随滑杆变化，把目标提到切点收益以上才会沿前沿上移（切点收益见「敏感性分析」曲线上开始下滑的那一点）。"
                                 : "实际收益 \(pct(result.portfolio.expectedReturn)) 已贴合目标下限 \(pct(target))。")
                                .font(.caption).foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }

                    // Weights bar chart
                    // 测试标的 (isTest) 用橙色区分: 它们是 what-if 临时注入的仓位, 不进执行清单,
                    // 但同样占权重 —— 必须出现在这张图里, 否则权重合计看起来不足 100%。
                    Text("资产权重").font(.headline)
                    Chart(result.assets.sorted(by: { $0.weight > $1.weight }), id: \.key) { a in
                        BarMark(
                            x: .value("权重", a.weight),
                            y: .value("标的", a.name)
                        )
                        .foregroundStyle(a.isTest == true ? AnyShapeStyle(.orange.gradient)
                                                          : AnyShapeStyle(.blue.gradient))
                        .annotation(position: .trailing) {
                            Text(pct(a.weight)).font(.caption2).monospacedDigit()
                        }
                    }
                    // 标的很多时不再无限变高（弹窗内滚动，图本身给个上限）
                    .frame(height: min(max(220, CGFloat(result.assets.count) * 28), 440))

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
                        DisclosureGroup(isExpanded: $showTestAssets) {
                            VStack(alignment: .leading, spacing: 6) {
                                Text("口径提示：测试标的的 μ 是纯历史年化（不做先验混合），组合内资产的 μ 是 50% 历史 + 50% 先验假设。"
                                     + "两者口径不同，横向比较时测试标的的预期收益偏乐观；图中橙色行即为测试标的。")
                                    .font(.caption).foregroundStyle(.secondary)
                                ForEach(ta, id: \.key) { e in
                                    VStack(alignment: .leading, spacing: 2) {
                                        HStack(spacing: 8) {
                                            Text(e.ticker).fontWeight(.medium)
                                            Text(classLabel(e.testClass))
                                                .font(.caption2).foregroundStyle(.orange)
                                            Text("\(e.source == "eastmoney" ? "天天基金" : "Yahoo") · \(e.pool == "domestic" ? "境内池" : "境外池") · \(windowLabel(e.windowYears)) · \(e.nDays) 天")
                                                .font(.caption).foregroundStyle(.secondary)
                                            Spacer()
                                            Text("μ \(pct(e.mu))   σ \(pct(e.vol))")
                                                .font(.caption).monospacedDigit().foregroundStyle(.secondary)
                                        }
                                        let note = provenanceNote(e)
                                        if !note.isEmpty {
                                            Text(note).font(.caption2).foregroundStyle(.secondary)
                                        }
                                    }
                                }
                            }
                        } label: {
                            Text("测试标的估算参数（\(ta.count) 只 · μ = 100% 历史年化）").font(.headline)
                        }
                    }

                    // 汇总自检/管线提示（含"权重未全覆盖"这类映射漏项告警）
                    if let ws = result.warnings, !ws.isEmpty {
                        Divider()
                        DisclosureGroup(isExpanded: $showWarnings) {
                            VStack(alignment: .leading, spacing: 4) {
                                ForEach(Array(ws.enumerated()), id: \.offset) { _, w in
                                    Text("· \(w)").font(.caption).foregroundStyle(.secondary)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                            }
                        } label: {
                            Text("优化器提示（\(ws.count)）").font(.headline)
                        }
                    }
                }
            }
            // 滚动到底也能关：底部再给一个关闭出口（Esc 同样有效）
            HStack {
                Spacer()
                Button("关闭") { dismiss() }.keyboardShortcut(.cancelAction)
            }
        }
        .padding()
        .frame(width: panelSize.width, height: panelSize.height)
        .onExitCommand { dismiss() }
        .sheet(isPresented: $showSensitivity) {
            if let sr = sensitivityResult {
                // 把主结果的实际落点一并带过去：图上一眼能看出"主结果就在这条曲线上"
                SensitivityAnalysisView(
                    result: sr,
                    currentTarget: result.targetReturn,
                    currentSharpe: result.portfolio.sharpe,
                    currentVol: result.portfolio.volatility)
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

    /// 带符号百分比（+2.31% / -1.05%），用于「实际 − 目标」的差额
    private func pctSigned(_ v: Double) -> String { String(format: "%+.2f%%", v * 100) }

    /// 「实际 − 目标」的告警阈值，与 Python 侧容差**同量级**（1e-4 = 0.01pp）。
    /// 优化器判定「交付收益 ≥ 目标」用 ret >= target - 1e-4（阶段一约束与收敛判据），
    /// 袖内收益下限用 SLEEVE_FLOOR_EPS = 1e-4（Optimizer/scripts/optimize_portfolio.py 里
    /// 唯一的容差常量）—— 两处都是 1e-4，所以这里必须同量级。
    /// 早先这里写 0.002（0.2pp）：差额落在 0.02–0.2pp 时告警文案不触发，
    /// 「略低于目标」会被显示成正常结果 —— 正是要堵的漏报。
    private static let targetGapTolerance = 1e-4

    /// warnings 里是否属于「目标未达成 / 交付受限」语义：回退、不可行、预算用尽、低于目标。
    /// 优化器只把这些语义写在 warnings 的文本里（结果 JSON 无结构化字段），所以这里做关键词归类；
    /// 不改公开接口、不改 Optimizer。判定口径与 Optimizer/scripts/optimize_portfolio.py 的
    /// 回退/不可行提示词一致（该文件是唯一事实来源，措辞改了这里也要跟着改）。
    private func isCriticalWarning(_ text: String) -> Bool {
        let keys = ["不可行", "不可交付", "已回退", "回退到", "回退使用", "回退/不可行",
                    "低于目标", "预算已用尽", "预算未用尽"]
        return keys.contains { text.contains($0) }
    }

    private var criticalWarnings: [String] {
        (result.warnings ?? []).filter(isCriticalWarning)
    }

    /// 「实际 − 目标」行（仅测试结果页）。主结果页只有"已贴合/高出"两种说法，
    /// 交付低于目标（回退角点）时会被读成"正常"，所以这里显式给出差额与未达目标结论。
    private func testTargetGapRow(target: Double) -> some View {
        let gap = result.targetGap ?? (result.portfolio.expectedReturn - target)
        // 阈值 = 优化器容差量级（见 targetGapTolerance 的说明），不再是 0.2pp
        let below = gap < -Self.targetGapTolerance
        return HStack(spacing: 6) {
            Image(systemName: below ? "exclamationmark.triangle.fill" : "info.circle")
                .foregroundStyle(below ? Color.red : Color.secondary)
            Text(below
                 ? "实际收益 \(pct(result.portfolio.expectedReturn)) − 目标下限 \(pct(target)) = \(pctSigned(gap))：「未达目标」，低于下限 \(pct(-gap))。目标在当前约束下不可行 —— 上面交付的是最大可行收益，不是目标组合。"
                 : "实际收益 \(pct(result.portfolio.expectedReturn)) − 目标下限 \(pct(target)) = \(pctSigned(gap))。目标只是收益「下限」（不是锚）：目标低于切点收益时结果不随滑杆变化。")
                .font(.caption).foregroundStyle(below ? Color.red : Color.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .combine)
    }

    /// 测试结果页常显警示条：回退 / 不可行 / 预算用尽 类提示不再只在折叠区内。
    private var criticalWarningBanner: some View {
        let shown = Array(criticalWarnings.prefix(3))
        return HStack(alignment: .top, spacing: 8) {
            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.red)
            VStack(alignment: .leading, spacing: 4) {
                Text("目标未达成 / 交付受限（\(criticalWarnings.count) 条）")
                    .font(.caption).fontWeight(.semibold)
                ForEach(Array(shown.enumerated()), id: \.offset) { _, w in
                    Text(w).font(.caption2).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if criticalWarnings.count > shown.count {
                    Text("另有 \(criticalWarnings.count - shown.count) 条同类提示，见下方「优化器提示」。")
                        .font(.caption2).foregroundStyle(.secondary)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(8)
        .background(Color.red.opacity(0.10), in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.red.opacity(0.35)))
        .accessibilityElement(children: .combine)
    }

    private func classLabel(_ cls: String?) -> String {
        switch cls {
        case "broad": return "宽基/大类"
        case "strategy": return "策略指数"
        case "active": return "个股/主动"
        default: return "类别未标注"
        }
    }

    private func windowLabel(_ years: Int?) -> String {
        guard let y = years else { return "窗口全历史" }
        return "窗口近 \(y) 年"
    }

    /// 说明这个 μ/σ 是怎么来的（口径与 asset_classes.py 一致）
    private func provenanceNote(_ e: TestAssetEstimate) -> String {
        var parts: [String] = []
        switch e.muNote {
        case "history_only":
            parts.append("本类标的在册时是「历史 + 人工先验」，此测试标的无先验 → μ 退化为纯历史，不能与组合内同类直接横比")
        case "prior_fallback":
            parts.append("无行情历史，μ 用人工先验兜底")
        case "fixed":
            parts.append("写死值")
        default:
            if let h = e.muHist { parts.append("历史年化 μ \(pct(h))") }
        }
        switch e.volNote {
        default:
            if let h = e.volHist, e.muNote != "prior_fallback" { parts.append("历史波动 σ \(pct(h))") }
        }
        return parts.joined(separator: "；")
    }
}

/// 新标的测试输入弹窗：逗号分隔临时标的 → AppStore.runTestOptimization。
/// 运行完成且成功时, 在本弹窗之上再弹出「测试结果」(store: nil 形态, 无重测入口)。
private struct NewTickerTestInputView: View {
    let store: AppStore
    @Environment(\.dismiss) private var dismiss
    @State private var input = ""
    @State private var showResult = false
    /// 标的类别。口径与 Python 侧 asset_classes.py 的 CLASS_PARAM_HELP 一致（改这里也要改那边）。
    @State private var testClass = "active"

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

            // 类别参数：决定 μ 怎么估、历史窗口多长、是否套人工波动率下限
            VStack(alignment: .leading, spacing: 4) {
                Picker("标的类别", selection: $testClass) {
                    Text("个股 / 主动基金　100% 历史年化，截断 −10%~40%，窗口 10 年，不套波动率下限")
                        .tag("active")
                    Text("策略指数（smart beta）　67% 历史 + 33% 先验，截断 −5%~30%，窗口 20 年")
                        .tag("strategy")
                    Text("宽基 / 大类（含债券、黄金、商品）　50% 历史 + 50% 先验，截断 0%~20%，窗口全历史")
                        .tag("broad")
                }
                .pickerStyle(.radioGroup)
                Text("类别决定两件事：① 预期收益怎么估（先验占比不同）；② 历史窗口多长。σ 一律取历史年化波动（没有人工下限）。\n个股 / 主动类还额外受「单个标的组合权重 ≤ 10%」约束（触顶会在结果里提示）。")
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Text("测试标的一律没有人工先验（只有登记在册的标的才有），所以选「宽基 / 策略」时预期收益会退化为纯历史年化，并给出提示——这与组合内同类标的的口径不同，不能直接横比。相关性 = 与各类资产代理指数的历史相关。6 位纯数字按境内基金（天天基金净值）处理并参与境内池约束；其余走 Yahoo 代码（美股 / 港股 0700.HK / 日股 7203.T / A股 600519.SS）。已在优化池内的标的会被跳过。")
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
                    store.runTestOptimization(tickers: parseInput(input), testClass: testClass)
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
