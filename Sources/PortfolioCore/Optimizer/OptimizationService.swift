import Foundation

/// A single optimization progress step (mirrors the Python sidecar's JSONL records).
public struct OptimizationStep: Codable, Hashable, Sendable {
    public let step: String
    public let message: String
    public let level: String
    public let ts: String
}

/// Final outcome of an optimization run.
public struct OptimizationOutcome {
    public let runID: Int64
    public let logPath: String
    public let result: OptimizationResult?
    public let steps: [OptimizationStep]
    public let error: String?
}

/// Capability 2: drives the Python optimizer through the sidecar, streams structured
/// step logs (JSONL, readable by AI), and persists runs/logs/result into SQLite.
///
/// Designed to be driven from a single serial context (the @MainActor AppStore, or the
/// CLI's main thread). `start` launches the subprocess without blocking; the caller
/// polls `pollSteps` until `isRunning == false`, then calls `finalize`.
public final class OptimizationService {
    public let db: Database
    public let sidecar: PythonSidecar
    public let logsDir: URL

    private var process: Process?
    private var runID: Int64?
    private var logPath: String?
    private var resultPath: String?
    private var emittedLines = 0
    private var logSeq = 0
    private var stderrPath: String?

    public init(db: Database, sidecar: PythonSidecar, logsDir: URL) {
        self.db = db
        self.sidecar = sidecar
        self.logsDir = logsDir
    }

    public var isRunning: Bool { process?.isRunning ?? false }

    // MARK: - 联网管线临时目录 (基金搜索易实时列表 + 天天基金实时申赎状态)

    /// 共享临时目录: 同一 App 会话里的优化运行与敏感性分析复用同一份联网产物。
    /// 优化每次运行都会重写这些文件 (每次完整联网), 所以无需按会话清理旧文件 —
    /// 上次崩溃残留会被下一次优化运行直接覆盖。
    public static func workDirURL() -> URL {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("PortfolioManager-optimizer-work", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    /// 关闭优化器时调用: 整体删除联网管线临时文件。
    public static func cleanupWorkDir() {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("PortfolioManager-optimizer-work", isDirectory: true)
        try? FileManager.default.removeItem(at: dir)
    }

    /// Launch the optimizer subprocess. Returns immediately with the run id.
    /// 每次运行都会完整联网: 实时抓取基金搜索易 → 天天基金实时校验申赎状态,
    /// 中间产物写入共享临时目录 (cleanupWorkDir 在优化器关闭时删除)。
    @discardableResult
    public func start(extractJSON: URL, totalAssets: Double? = nil,
                      targetReturn: Double = 0.10,
                      testTickers: [String]? = nil,
                      testClass: String = "active") throws -> Int64 {
        guard process == nil || process?.isRunning == false else {
            throw SidecarError.nonZeroExit(code: -1, stderr: "an optimization is already running")
        }
        try FileManager.default.createDirectory(at: logsDir, withIntermediateDirectories: true)

        let run = OptimizationRun(startedAt: Self.isoNow(), status: "running")
        let id = try db.insertRun(run)

        let logPath = logsDir.appendingPathComponent("run_\(id).jsonl").path
        let resultPath = logsDir.appendingPathComponent("result_\(id).json").path
        let detailPath = logsDir.appendingPathComponent("detail_\(id).json").path
        let stderrPath = logsDir.appendingPathComponent("stderr_\(id).log").path

        // reset per-run state
        self.runID = id
        self.logPath = logPath
        self.resultPath = resultPath
        self.stderrPath = stderrPath
        self.emittedLines = 0

        // capture run params hash
        var paramsDesc = "target=\(targetReturn);extract=\(extractJSON.lastPathComponent)"
        if let t = totalAssets { paramsDesc += ";total=\(t)" }
        if let tt = testTickers, !tt.isEmpty {
            paramsDesc += ";test=\(tt.joined(separator: ","));testClass=\(testClass)"
        }
        try db.updateRun(id: id, finishedAt: nil, status: "running", resultJSON: nil)

        var args = [
            extractJSON.path,
            "--result-json", resultPath,
            "--json", detailPath,
            "--log", logPath,
            "--work-dir", Self.workDirURL().path,
            // 联网管线总预算: 分组早停校验实测 ~15-40s, 预留重试余量.
            "--check-timeout", "300",
            // App 侧目标收益率 (滑块) 必须显式传给 Python, 否则脚本用 params.py 硬编码 0.10.
            "--target-return", String(targetReturn),
        ]
        if let t = totalAssets { args += ["--total-assets", String(t)] }
        if let tt = testTickers, !tt.isEmpty {
            args += ["--test-tickers", tt.joined(separator: ",")]
            // 标的类别决定 μ 怎么估（宽基=半历史半先验 / 策略=2/3 历史 / 个股=纯历史），
            // 也决定是否套用波动率下限与 10% 单标的上限。缺省 active。
            args += ["--test-class", testClass]
        }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: sidecar.interpreterPath)
        process.arguments = [sidecar.scriptURL("optimize_portfolio.py").path] + args
        if let cwd = sidecar.currentDirectoryURL { process.currentDirectoryURL = cwd }
        process.environment = ProcessInfo.processInfo.environment

        // redirect stdout/stderr to files to avoid pipe back-pressure
        FileManager.default.createFile(atPath: detailPath, contents: nil)
        FileManager.default.createFile(atPath: stderrPath, contents: nil)
        let errHandle = try FileHandle(forWritingTo: URL(fileURLWithPath: stderrPath))
        process.standardError = errHandle
        process.standardOutput = FileHandle.nullDevice

        try process.run()
        self.process = process
        try db.updateRun(id: id, finishedAt: nil, status: "running", resultJSON: nil)
        return id
    }

    /// Read newly appended JSONL steps since the last poll.
    public func pollSteps() -> [OptimizationStep] {
        guard let logPath = logPath else { return [] }
        guard let text = try? String(contentsOfFile: logPath, encoding: .utf8) else { return [] }
        let lines = text.split(separator: "\n", omittingEmptySubsequences: true).map(String.init)
        guard lines.count > emittedLines else { return [] }
        let newLines = Array(lines[emittedLines...])
        emittedLines = lines.count
        var steps: [OptimizationStep] = []
        for line in newLines {
            if let data = line.data(using: .utf8),
               let obj = try? JSONDecoder().decode(OptimizationStep.self, from: data) {
                steps.append(obj)
            }
        }
        // persist to DB (best-effort; ignore errors to keep polling cheap)
        if let id = runID {
            for s in steps {
                logSeq += 1
                try? db.insertLog(runID: id, seq: logSeq,
                                  step: s.step, message: s.message, level: s.level, ts: s.ts)
            }
        }
        return steps
    }

    /// After the subprocess exits, parse the result and finalize the DB run row.
    public func finalize() throws -> OptimizationOutcome {
        guard let id = runID, let logPath = logPath else {
            throw SidecarError.nonZeroExit(code: -1, stderr: "no run in progress")
        }
        // drain remaining steps
        _ = pollSteps()

        let status = process?.terminationStatus ?? -1
        var result: OptimizationResult? = nil
        var error: String? = nil

        if status == 0, let rp = resultPath,
           let data = try? Data(contentsOf: URL(fileURLWithPath: rp)) {
            let decoder = JSONDecoder()
            decoder.keyDecodingStrategy = .convertFromSnakeCase
            result = try? decoder.decode(OptimizationResult.self, from: data)
        } else {
            let err = (try? String(contentsOfFile: stderrPath ?? "", encoding: .utf8)) ?? ""
            error = "optimizer exited \(status): \(err)"
        }

        let finished = Self.isoNow()
        try db.updateRun(id: id, finishedAt: finished,
                         status: result != nil ? "completed" : "failed",
                         resultJSON: result.map { r -> String in
                             (try? String(data: JSONEncoder().encode(r), encoding: .utf8)) ?? ""
                         })
        process = nil
        return OptimizationOutcome(runID: id, logPath: logPath, result: result,
                                   steps: (try? db.fetchLogs(runID: id).map {
                                       OptimizationStep(step: $0.step, message: $0.message,
                                                        level: $0.level, ts: $0.ts)
                                   }) ?? [], error: error)
    }

    /// Convenience: run synchronously to completion (used by pm-cli).
    public func runSync(extractJSON: URL, totalAssets: Double? = nil,
                        targetReturn: Double = 0.10,
                        testTickers: [String]? = nil,
                        testClass: String = "active") throws -> OptimizationOutcome {
        _ = try start(extractJSON: extractJSON, totalAssets: totalAssets,
                      targetReturn: targetReturn, testTickers: testTickers, testClass: testClass)
        while isRunning {
            _ = pollSteps()
            Thread.sleep(forTimeInterval: 0.3)
        }
        return try finalize()
    }

    /// Run sensitivity analysis: step target return from 5% to max feasible, 1% increments.
    /// 复用优化运行留在共享临时目录的联网产物 (基金搜索易 + 天天基金状态),
    /// 不重新联网; 临时文件缺失/过期时由 Python 侧全量重跑管线。
    ///
    /// - Parameters:
    ///   - anchorTarget: 主结果的目标收益率。并入扫描网格，保证「主结果锚点」在图上有点可落。
    ///   - anchorSleeve: 主结果的袖内参数 {袖 key: [μ, σ]}（紧凑 JSON 传给 `--anchor-sleeve`）。
    ///     锚定目标点用它当 canonical 候选初值，与主结果同一口径。
    ///   - anchorRet / anchorVol: 主结果的实际收益 / 波动，供 Python 侧做锚点一致性检查。
    ///   全部默认 nil → 不追加任何 CLI 参数，与旧行为一致。
    public func runSensitivityAnalysis(extractJSON: URL,
                                       anchorTarget: Double? = nil,
                                       anchorSleeve: [String: [Double]]? = nil,
                                       anchorRet: Double? = nil,
                                       anchorVol: Double? = nil) throws -> SensitivityAnalysisResult {
        var args = [
            extractJSON.path,
            "--work-dir", Self.workDirURL().path,
            "--step-min", "0.05",
        ]
        // 锚点参数全部是加法式: 缺省不出现 → Python 侧走旧路径（求解次数/点字段不变）。
        if let t = anchorTarget { args += ["--anchor-target", String(t)] }
        if let sleeve = anchorSleeve, !sleeve.isEmpty,
           let sleeveJSON = Self.compactJSON(sleeve) {
            args += ["--anchor-sleeve", sleeveJSON]
        }
        if let r = anchorRet { args += ["--anchor-ret", String(r)] }
        if let v = anchorVol { args += ["--anchor-vol", String(v)] }

        let stdout = try sidecar.run(script: "sensitivity_analysis.py", args: args)

        guard let data = stdout.data(using: .utf8) else {
            throw SidecarError.nonZeroExit(code: -1, stderr: "sensitivity analysis produced no output")
        }

        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let result = try decoder.decode(SensitivityAnalysisResult.self, from: data)
        return result
    }

    /// 袖内参数 → 紧凑 JSON 字符串。键排序保证同一输入产生同一条命令行（可复现）。
    private static func compactJSON(_ value: [String: [Double]]) -> String? {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        guard let data = try? encoder.encode(value) else { return nil }
        return String(data: data, encoding: .utf8)
    }

    private static func isoNow() -> String {
        ISO8601DateFormatter().string(from: Date())
    }
}
