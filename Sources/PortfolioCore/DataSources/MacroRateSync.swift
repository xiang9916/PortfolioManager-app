import Foundation

/// 中美 10 年期国债收益率（宏观 RF 的数据来源）。
public struct MacroRates: Sendable, Equatable {
    public let cn10y: Double
    public let us10y: Double
    public let date: String

    public init(cn10y: Double, us10y: Double, date: String) {
        self.cn10y = cn10y
        self.us10y = us10y
        self.date = date
    }
}

/// 宏观利率同步（能力2 的 RF 数据来源）。
///
/// 改造前这段逻辑直接写在 `AppStore.refreshMacroRates()` 里：自己拼 `Process`、
/// 同步 `waitUntilExit()` —— 每次启动/刷新都会把主线程卡住几百毫秒到几秒（ADR 0011）。
/// 现在：解析是纯函数、子进程在后台线程跑、落库回到调用方。
@MainActor
public enum MacroRateSync {

    /// 解析 `fetch_macro_rates.py` 的 stdout（三个字段缺一不可）。
    nonisolated public static func parse(_ stdout: String) -> MacroRates? {
        guard let data = stdout.data(using: .utf8),
              let obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let cn = obj["cn_10y"] as? Double,
              let us = obj["us_10y"] as? Double,
              let date = obj["date"] as? String else { return nil }
        return MacroRates(cn10y: cn, us10y: us, date: date)
    }

    /// 取一次宏观利率并写进 `macro_rates`。失败是**非致命**的：保留旧值，由
    /// `params.py` 的 RF 兜底逻辑接管。
    ///
    /// - Parameter run: 子进程调用面（测试注入假输出）。默认在**后台线程**执行。
    public static func refresh(config: SidecarConfig, into db: Database,
                               run: @escaping @Sendable (SidecarConfig) throws -> String = { config in
        let sidecar = PythonSidecar(interpreterPath: config.interpreterPath,
                                    scriptsDir: config.scriptsDir,
                                    currentDirectoryURL: config.currentDirectoryURL)
        return try sidecar.run(script: "fetch_macro_rates.py", args: [])
    }) async {
        let scriptPath = config.scriptsDir.appendingPathComponent("fetch_macro_rates.py").path
        guard FileManager.default.fileExists(atPath: config.interpreterPath),
              FileManager.default.fileExists(atPath: scriptPath) else { return }

        // 阻塞式子进程调用放到后台 —— 不要在主线程 waitUntilExit。
        let stdout = await Task.detached { try? run(config) }.value
        guard let stdout, let rates = parse(stdout) else { return }

        try? db.upsertMacroRate("cn_10y", value: rates.cn10y, asOfDate: rates.date, source: "akshare")
        try? db.upsertMacroRate("us_10y", value: rates.us10y, asOfDate: rates.date, source: "akshare")
    }
}
