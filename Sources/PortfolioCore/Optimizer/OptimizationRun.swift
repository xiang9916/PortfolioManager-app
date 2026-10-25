import Foundation

/// 一次优化运行的输入。
public struct OptimizationRequest: Sendable {
    public let extract: URL
    public let targetReturn: Double
    /// 非空 = 「新标的测试」运行。
    public let testTickers: [String]
    /// 测试标的类别（broad / strategy / active）。
    public let testClass: String

    public init(extract: URL, targetReturn: Double,
                testTickers: [String] = [], testClass: String = "active") {
        self.extract = extract
        self.targetReturn = targetReturn
        self.testTickers = testTickers
        self.testClass = testClass
    }
}

/// 运行过程中的事件（顺序：started → steps… → finished / failed）。
public enum OptimizationEvent {
    case started
    case steps([OptimizationStep])
    case finished(OptimizationOutcome)
    case failed(String)
}

/// 优化的驱动面 —— 唯一实现是 `OptimizationService`，测试用假实现替代。
///
/// 这么做是为了让「一次优化运行」成为可测的 module：过去它是 `AppStore` 里两条
/// 85 行的 300ms 轮询循环，App 层没有 testTarget，只能人眼验证（ADR 0011）。
public protocol OptimizationDriving: AnyObject {
    var isRunning: Bool { get }
    func start(extractJSON: URL, totalAssets: Double?, targetReturn: Double,
               testTickers: [String]?, testClass: String) throws -> Int64
    func pollSteps() -> [OptimizationStep]
    func finalize() throws -> OptimizationOutcome
}

extension OptimizationService: OptimizationDriving {}

/// 「一次优化运行」的编排：启动 → 轮询 → 收尾。主运行与新标的测试共用同一份实现。
///
/// 语义与改造前的两条内联循环逐条对齐：
/// - `isBusy` 在整段运行期间为 true（调用方据此互斥主运行与测试运行）；
/// - 每轮先 `pollSteps()` 再等 `pollInterval`，退出条件是 `isRunning == false`；
/// - 收尾一定调用 `finalize()`（它会 drain 剩余步骤）。
@MainActor
public final class OptimizationRunner {
    public private(set) var isBusy = false

    private let driver: any OptimizationDriving
    private let pollInterval: Duration
    private let sleep: @MainActor (Duration) async -> Void

    public init(driver: any OptimizationDriving,
                pollInterval: Duration = .milliseconds(300),
                sleep: @escaping @MainActor (Duration) async -> Void = { try? await Task.sleep(for: $0) }) {
        self.driver = driver
        self.pollInterval = pollInterval
        self.sleep = sleep
    }

    /// 跑一次优化直到结束。事件按顺序回调；已在运行时立刻 `.failed` 返回，不启动子进程。
    public func run(_ request: OptimizationRequest,
                    onEvent: @MainActor (OptimizationEvent) -> Void) async {
        guard !isBusy else {
            onEvent(.failed("an optimization is already running"))
            return
        }
        isBusy = true
        defer { isBusy = false }

        onEvent(.started)
        do {
            _ = try driver.start(extractJSON: request.extract,
                                 totalAssets: nil,
                                 targetReturn: request.targetReturn,
                                 testTickers: request.testTickers.isEmpty ? nil : request.testTickers,
                                 testClass: request.testClass)
        } catch {
            onEvent(.failed(String(describing: error)))
            return
        }

        while driver.isRunning {
            let fresh = driver.pollSteps()
            if !fresh.isEmpty { onEvent(.steps(fresh)) }
            await sleep(pollInterval)
        }

        do {
            onEvent(.finished(try driver.finalize()))
        } catch {
            onEvent(.failed(String(describing: error)))
        }
    }
}

// MARK: - 敏感性分析

/// 调 Python 子进程所需的三个不可变参数（可安全跨线程传递）。
public struct SidecarConfig: Sendable {
    public let interpreterPath: String
    public let scriptsDir: URL
    public let currentDirectoryURL: URL?

    public init(interpreterPath: String, scriptsDir: URL, currentDirectoryURL: URL?) {
        self.interpreterPath = interpreterPath
        self.scriptsDir = scriptsDir
        self.currentDirectoryURL = currentDirectoryURL
    }
}

/// 敏感性分析的锚点参数（全部可选：旧结果缺这些字段时不传，走旧路径）。
public struct SensitivityAnchor: Sendable {
    public let targetReturn: Double?
    public let sleeve: [String: [Double]]?
    public let actualReturn: Double?
    public let volatility: Double?

    public init(targetReturn: Double? = nil, sleeve: [String: [Double]]? = nil,
                actualReturn: Double? = nil, volatility: Double? = nil) {
        self.targetReturn = targetReturn
        self.sleeve = sleeve
        self.actualReturn = actualReturn
        self.volatility = volatility
    }
}
