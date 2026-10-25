import Foundation
import PortfolioCore

/// App 层接触外部世界的**唯一**入口：路径、UserDefaults、时钟、子进程参数。
///
/// 默认值就是原来的 `AppPaths` 与 `UserDefaults.standard`（行为逐字不变）；
/// 抽成可注入的值之后，装配逻辑（数据库位置、日志目录、目标收益率持久化）不再
/// 散落在 View 与 AppStore 里，也能在测试/preview 里替换（ADR 0011）。
public struct AppEnvironment {

    /// 优化目标收益率的 UserDefaults 键（视图不再直接读写它）。
    public static let targetReturnKey = "optimizer.targetReturn"

    public let databaseURL: URL
    public let extractURL: URL
    public let liveExtractURL: URL
    public let backupsURL: URL
    public let logsDir: URL
    public let sidecarConfig: SidecarConfig
    public let defaults: UserDefaults
    public let calendar: Calendar
    public let now: () -> Date

    public init(databaseURL: URL, extractURL: URL, liveExtractURL: URL,
                backupsURL: URL, logsDir: URL, sidecarConfig: SidecarConfig,
                defaults: UserDefaults, calendar: Calendar,
                now: @escaping () -> Date) {
        self.databaseURL = databaseURL
        self.extractURL = extractURL
        self.liveExtractURL = liveExtractURL
        self.backupsURL = backupsURL
        self.logsDir = logsDir
        self.sidecarConfig = sidecarConfig
        self.defaults = defaults
        self.calendar = calendar
        self.now = now
    }

    /// 当前进程的默认环境（与改造前的静态调用等价）。
    public static var current: AppEnvironment {
        AppEnvironment(
            databaseURL: AppPaths.databaseURL(),
            extractURL: AppPaths.extractJSONURL(),
            liveExtractURL: AppPaths.liveExtractURL(),
            backupsURL: AppPaths.backupsURL(),
            logsDir: URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
                .appendingPathComponent("tmp/optimizer_logs", isDirectory: true),
            sidecarConfig: AppPaths.sidecarConfig(),
            defaults: .standard,
            calendar: .current,
            now: Date.init)
    }

    /// 按当前环境构造一个 Python 子进程入口（解释器 / 脚本目录 / 工作目录）。
    public func makeSidecar() -> PythonSidecar {
        PythonSidecar(interpreterPath: sidecarConfig.interpreterPath,
                      scriptsDir: sidecarConfig.scriptsDir,
                      currentDirectoryURL: sidecarConfig.currentDirectoryURL)
    }
}
