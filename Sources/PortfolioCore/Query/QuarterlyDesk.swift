import Foundation
import Observation

/// 财务分析底稿（能力4）的状态与落盘节奏。
///
/// 这里只做「一列一季」的增删改与 0.6s 防抖落盘；派生公式仍在 `QuarterlyMetrics`、
/// 一键填入口径仍在 `QuarterlyFill`。抽出来的理由（ADR 0011）：防抖 + 删除必须取消
/// 未落盘快照这类时序规则以前写在 App 层，无法单测，而它一旦错了会把已删的行写回去。
@MainActor
@Observable
public final class QuarterlyDesk {

    /// 结构性操作的结果（中文文案由 App 侧映射，Core 不写 UI 文案）。
    public enum Outcome: Equatable {
        case added(periodEnd: String)
        case duplicate(periodEnd: String)
        case deleted(periodEnd: String)
        case renamed(to: String)
        case unchanged
        case failed(String)
    }

    /// 落盘面（唯一实现是 `Database`；测试注入假实现）。
    public struct Persistence {
        public let upsert: ([QuarterlyReport]) throws -> Void
        public let remove: (String) throws -> Void
        public let rename: (String, String) throws -> Void

        public init(upsert: @escaping ([QuarterlyReport]) throws -> Void,
                    remove: @escaping (String) throws -> Void,
                    rename: @escaping (String, String) throws -> Void) {
            self.upsert = upsert
            self.remove = remove
            self.rename = rename
        }
    }

    public private(set) var reports: [QuarterlyReport] = []
    /// 后台防抖落盘失败时回调（store 用来写 statusMessage）。
    @ObservationIgnored public var onPersistFailure: (@MainActor (String) -> Void)?

    private let persistence: Persistence
    private let debounce: Duration
    private let sleep: @MainActor (Duration) async -> Void
    private var pending: Task<Void, Never>?

    public init(persistence: Persistence,
                debounce: Duration = .milliseconds(600),
                sleep: @escaping @MainActor (Duration) async -> Void = { try? await Task.sleep(for: $0) }) {
        self.persistence = persistence
        self.debounce = debounce
        self.sleep = sleep
    }

    /// 用库里的整表替换（`loadAll()`）。取消未落盘的防抖，避免旧快照把行复活。
    public func replaceAll(_ reports: [QuarterlyReport]) {
        pending?.cancel()
        pending = nil
        self.reports = reports
    }

    // MARK: - 编辑

    /// 网格内联编辑：内存立即生效（派生行实时重算），落盘防抖。
    public func update(periodEnd: String, field: QuarterlyField, value: Double?) {
        guard let i = reports.firstIndex(where: { $0.periodEnd == periodEnd }) else { return }
        reports[i][keyPath: field.keyPath] = value
        schedulePersist()
    }

    /// 添加一个季度列（默认 = 最后一列之后的下一个季末；首列 = 当前季末）。
    @discardableResult
    public func add() -> Outcome {
        let next: String
        if let last = reports.map(\.periodEnd).max(),
           let following = QuarterlyMetrics.nextQuarterEnd(after: last) {
            next = following
        } else {
            next = QuarterlyMetrics.currentQuarterEnd()
        }
        guard !reports.contains(where: { $0.periodEnd == next }) else {
            return .duplicate(periodEnd: next)
        }
        reports.append(QuarterlyReport(periodEnd: next, source: "manual"))
        reports.sort { $0.periodEnd < $1.periodEnd }
        if let message = flushNow() { return .failed(message) }
        return .added(periodEnd: next)
    }

    /// 删除一列。**必须先取消未落盘的快照**，否则旧快照会把删掉的行写回来。
    @discardableResult
    public func delete(periodEnd: String) -> Outcome {
        pending?.cancel()
        pending = nil
        reports.removeAll { $0.periodEnd == periodEnd }
        do {
            try persistence.remove(periodEnd)
            return .deleted(periodEnd: periodEnd)
        } catch {
            return .failed(String(describing: error))
        }
    }

    /// 修改列的截止日（表头编辑）。
    @discardableResult
    public func rename(from oldEnd: String, to newEnd: String) -> Outcome {
        let trimmed = newEnd.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty, trimmed != oldEnd else { return .unchanged }
        guard !reports.contains(where: { $0.periodEnd == trimmed }) else {
            return .duplicate(periodEnd: trimmed)
        }
        do {
            try persistence.rename(oldEnd, trimmed)
            pending?.cancel()
            pending = nil
            if let i = reports.firstIndex(where: { $0.periodEnd == oldEnd }) {
                reports[i].periodEnd = trimmed
                reports.sort { $0.periodEnd < $1.periodEnd }
            }
            if let message = flushNow() { return .failed(message) }
            return .renamed(to: trimmed)
        } catch {
            return .failed(String(describing: error))
        }
    }

    /// 立即落盘（导出备份 / 结构性操作后调用）。
    public func flush() {
        if let message = flushNow() { onPersistFailure?(message) }
    }

    // MARK: - 落盘

    /// 防抖落盘：停止输入 0.6s 后整表 upsert（表小，整表写无压力）。
    private func schedulePersist() {
        pending?.cancel()
        let snapshot = reports
        pending = Task { [weak self] in
            guard let self else { return }
            await self.sleep(self.debounce)
            guard !Task.isCancelled else { return }
            guard let message = self.persist(snapshot) else { return }
            self.onPersistFailure?(message)
        }
    }

    private func flushNow() -> String? {
        pending?.cancel()
        pending = nil
        return persist(reports)
    }

    private func persist(_ snapshot: [QuarterlyReport]) -> String? {
        do {
            try persistence.upsert(snapshot)
            return nil
        } catch {
            return String(describing: error)
        }
    }
}
