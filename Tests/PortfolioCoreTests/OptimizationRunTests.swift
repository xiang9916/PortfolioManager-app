import XCTest
@testable import PortfolioCore

/// `OptimizationRunner` 的行为测试：事件顺序、忙互斥、失败路径、参数转发。
///
/// 用假驱动（`OptimizationDriving`）替代 Python 子进程 —— 改造前这两条流程在
/// `AppStore` 里是 85 行内联循环，App 层没有 testTarget，只能人眼验证（ADR 0011）。
@MainActor
final class OptimizationRunTests: XCTestCase {

    // MARK: - 事件顺序

    func testEmitsEventsInOrderAndFinalizesOnce() async {
        let driver = FakeDriver()
        driver.stepsToEmit = 2
        let runner = OptimizationRunner(driver: driver, pollInterval: .zero, sleep: { _ in })

        var events: [String] = []
        var steps: [OptimizationStep] = []
        await runner.run(request) { event in
            switch event {
            case .started: events.append("started")
            case .steps(let fresh): events.append("steps"); steps.append(contentsOf: fresh)
            case .finished: events.append("finished")
            case .failed(let message): events.append("failed:" + message)
            }
        }

        XCTAssertEqual(events, ["started", "steps", "steps", "finished"])
        XCTAssertEqual(steps.count, 2)
        XCTAssertEqual(driver.finalizeCount, 1)
        XCTAssertFalse(runner.isBusy)
    }

    // MARK: - 参数转发

    func testForwardsRequestFields() async {
        let driver = FakeDriver()
        let runner = OptimizationRunner(driver: driver, pollInterval: .zero, sleep: { _ in })
        let testRequest = OptimizationRequest(extract: URL(fileURLWithPath: "/tmp/e.json"),
                                              targetReturn: 0.2,
                                              testTickers: ["FICT-1"], testClass: "broad")
        await runner.run(testRequest) { _ in }

        let call = driver.startCalls.first
        XCTAssertEqual(call?.extract.lastPathComponent, "e.json")
        XCTAssertEqual(call?.target, 0.2)
        XCTAssertEqual(call?.tickers, ["FICT-1"])
        XCTAssertEqual(call?.testClass, "broad")
        XCTAssertNil(call?.totalAssets)
    }

    func testEmptyTestTickersBecomeNil() async {
        let driver = FakeDriver()
        let runner = OptimizationRunner(driver: driver, pollInterval: .zero, sleep: { _ in })
        await runner.run(request) { _ in }
        XCTAssertNil(driver.startCalls.first?.tickers, "空数组必须传 nil，不能变成 --test-tickers ''")
    }

    // MARK: - 失败路径

    func testStartFailureEmitsFailedAndSkipsPolling() async {
        struct Boom: Error {}
        let driver = FakeDriver()
        driver.startError = Boom()
        let runner = OptimizationRunner(driver: driver, pollInterval: .zero, sleep: { _ in })

        var failures = 0
        var finished = 0
        await runner.run(request) { event in
            switch event {
            case .failed: failures += 1
            case .finished: finished += 1
            default: break
            }
        }

        XCTAssertEqual(failures, 1)
        XCTAssertEqual(finished, 0)
        XCTAssertEqual(driver.pollCount, 0, "启动失败后不得继续轮询")
        XCTAssertEqual(driver.finalizeCount, 0)
        XCTAssertFalse(runner.isBusy)
    }

    func testFinalizeFailureEmitsFailed() async {
        struct Boom: Error {}
        let driver = FakeDriver()
        driver.finalizeError = Boom()
        let runner = OptimizationRunner(driver: driver, pollInterval: .zero, sleep: { _ in })

        var failures = 0
        await runner.run(request) { event in
            if case .failed = event { failures += 1 }
        }
        XCTAssertEqual(failures, 1)
        XCTAssertEqual(driver.finalizeCount, 1)
        XCTAssertFalse(runner.isBusy)
    }

    // MARK: - 忙互斥

    func testIsBusyWhileRunningAndResetAfter() async {
        let driver = FakeDriver()
        driver.stepsToEmit = 5
        let gate = Gate()
        let runner = OptimizationRunner(driver: driver, pollInterval: .zero,
                                        sleep: { _ in await gate.wait() })

        let task = Task { await runner.run(request) { _ in } }
        await waitUntil { runner.isBusy }
        XCTAssertTrue(runner.isBusy)
        gate.release()
        await task.value
        XCTAssertFalse(runner.isBusy)
    }

    func testSecondRunWhileBusyIsRejected() async {
        let driver = FakeDriver()
        driver.stepsToEmit = 5
        let gate = Gate()
        let runner = OptimizationRunner(driver: driver, pollInterval: .zero,
                                        sleep: { _ in await gate.wait() })

        let first = Task { await runner.run(request) { _ in } }
        await waitUntil { runner.isBusy }

        var rejections: [String] = []
        await runner.run(request) { event in
            if case .failed(let message) = event { rejections.append(message) }
        }
        XCTAssertEqual(rejections, ["an optimization is already running"])
        XCTAssertEqual(driver.startCalls.count, 1, "被拒的运行不得再启动子进程")

        gate.release()
        await first.value
    }

    // MARK: - helpers

    private var request: OptimizationRequest {
        OptimizationRequest(extract: URL(fileURLWithPath: "/tmp/extract.json"), targetReturn: 0.1)
    }

    private func waitUntil(_ condition: @MainActor () -> Bool) async {
        for _ in 0..<10_000 {
            if condition() { return }
            await Task.yield()
        }
        XCTFail("等待条件超时")
    }

    /// 让轮询循环停在 sleep 上（测试里可精确控制「运行中」这个状态）。
    @MainActor
    private final class Gate {
        private var waiters: [CheckedContinuation<Void, Never>] = []
        private var open = false

        func wait() async {
            if open { return }
            await withCheckedContinuation { waiters.append($0) }
        }

        func release() {
            open = true
            for w in waiters { w.resume() }
            waiters.removeAll()
        }
    }

    private final class FakeDriver: OptimizationDriving {
        var isRunning = false
        var startError: Error?
        var finalizeError: Error?
        var stepsToEmit = 1
        private(set) var startCalls: [(extract: URL, totalAssets: Double?, target: Double,
                                       tickers: [String]?, testClass: String)] = []
        private(set) var pollCount = 0
        private(set) var finalizeCount = 0

        func start(extractJSON: URL, totalAssets: Double?, targetReturn: Double,
                   testTickers: [String]?, testClass: String) throws -> Int64 {
            startCalls.append((extractJSON, totalAssets, targetReturn, testTickers, testClass))
            if let startError { throw startError }
            isRunning = true
            return 42
        }

        func pollSteps() -> [OptimizationStep] {
            pollCount += 1
            if stepsToEmit > 0 {
                stepsToEmit -= 1
                return [OptimizationStep(step: "s\(pollCount)", message: "m",
                                         level: "info", ts: "2026-01-01T00:00:00Z")]
            }
            isRunning = false
            return []
        }

        func finalize() throws -> OptimizationOutcome {
            finalizeCount += 1
            if let finalizeError { throw finalizeError }
            return OptimizationOutcome(runID: 42, logPath: "/tmp/fake.jsonl",
                                       result: nil, steps: [], error: nil)
        }
    }
}
