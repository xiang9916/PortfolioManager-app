import Foundation

/// 优化器告警的严重度（供结果页决定是否常显警示条）。
public enum OptimizationWarningSeverity: String, Sendable, CaseIterable {
    /// 交付被降级 / 目标未达成：必须常显，不得折叠。
    case critical
    /// 口径或约束被调整、结论有保留：可折叠展示。
    case warning
    /// 纯说明性信息（如测试标的被跳过、口径提示）。
    case info

    /// 供 UI 排序用：数字越大越严重。
    public var rank: Int {
        switch self {
        case .critical: return 2
        case .warning: return 1
        case .info: return 0
        }
    }
}

/// 优化器告警的稳定分类。
///
/// **事实来源**：Optimizer/scripts/optimize_portfolio.py 的 WARNING_CODES 表
/// （2026-10-17 快照：19 个 code，含未分类码 other；与表逐项 diff 一致）。新增 code 必须先在那里登记，
/// 再往这里加 case；文案改动不影响分类（新路径只认 code）。
public enum OptimizationWarningKind: String, Sendable, CaseIterable {
    case targetStage1Fallback = "target.stage1_fallback"
    case targetInfeasible = "target.infeasible"
    case targetBudgetExhausted = "target.budget_exhausted"
    case targetSleeveUnreachable = "target.sleeve_unreachable"
    case sleeveNotConverged = "sleeve.not_converged"
    case sleeveUndeliverable = "sleeve.undeliverable"
    case sleeveFloorInfeasible = "sleeve.floor_infeasible"
    case sleeveFloorPostcheckFailed = "sleeve.floor_postcheck_failed"
    case sleeveLevelsLifted = "sleeve.levels_lifted"
    case sleeveExcludedNoBasket = "sleeve.excluded_no_basket"
    case sleeveStage2Error = "sleeve.stage2_error"
    case sleeveUSUnavailable = "sleeve.us_unavailable"
    case activeCapPoolConflict = "active_cap.pool_conflict"
    case activeCapHit = "active_cap.hit"
    case summaryMissingWeights = "summary.missing_weights"
    case summaryWeightSumMismatch = "summary.weight_sum_mismatch"
    case fundAvailableNone = "fund.available_none"
    case testTickerSkipped = "test.ticker_skipped"
    case other = "other"

    /// 严重度映射（与旧视图关键词口径对齐；括号内为修正说明）。
    ///
    /// - critical：交付被降级、目标未达成，或交付结果不自洽 —— 回退、不可行、预算用尽、
    ///   袖不可交付、袖内下限拿不到 / 后校验失败、美股核心统计回退到阶段一、
    ///   汇总自检失败（权重漏项 / 合计 ≠ 1，照着执行会出错，不能只折叠展示）。
    ///   （旧关键词对 target.sleeve_unreachable 的文案漏报，code 路径修正：
    ///   它明确说"目标仍不可达、走了回退路径"，属 critical。）
    /// - warning：口径/约束被调整或结论有保留 —— 未完全收敛、袖被剔除后重解、
    ///   阶段二诊断、active 上限放宽 / 触顶、无可申购基金、未分类码。
    /// - info：纯说明 —— 袖水平被抬升后按请求目标重解（**不是**回退，交付未降级；
    ///   旧关键词因为它含"不可行"三字误报成 critical，code 路径修正）、测试标的跳过。
    public var severity: OptimizationWarningSeverity {
        switch self {
        case .targetStage1Fallback, .targetInfeasible, .targetBudgetExhausted,
             .targetSleeveUnreachable, .sleeveUndeliverable, .sleeveFloorInfeasible,
             .sleeveFloorPostcheckFailed, .sleeveUSUnavailable,
             .summaryMissingWeights, .summaryWeightSumMismatch:
            return .critical
        case .sleeveNotConverged, .sleeveExcludedNoBasket, .sleeveStage2Error,
             .activeCapPoolConflict, .activeCapHit, .fundAvailableNone, .other:
            return .warning
        case .sleeveLevelsLifted, .testTickerSkipped:
            return .info
        }
    }

    /// 优化器登记过的全部 code（含 other）。
    public static var allCodes: [String] { allCases.map(\.rawValue) }
}

/// 优化器输出的加法式告警明细项（对应结果 JSON 的 warning_items）。
public struct OptimizationWarningItem: Codable, Hashable, Sendable {
    public let code: String
    public let message: String

    public init(code: String, message: String) {
        self.code = code
        self.message = message
    }
}

/// 分类后的告警：结果页只读 kind / severity，不再匹配文案。
public struct OptimizationWarning: Equatable, Sendable {
    public let kind: OptimizationWarningKind
    public let severity: OptimizationWarningSeverity
    /// 原始 code；旧输出（无 warning_items）为 nil。
    public let code: String?
    public let message: String
    /// true = 按 code 分类（新路径）；false = 未知 code 或旧输出的文案兜底（兼容路径）。
    public let classifiedByCode: Bool

    public init(kind: OptimizationWarningKind, severity: OptimizationWarningSeverity,
                code: String?, message: String, classifiedByCode: Bool) {
        self.kind = kind
        self.severity = severity
        self.code = code
        self.message = message
        self.classifiedByCode = classifiedByCode
    }
}

/// 告警分类器。
public enum OptimizationWarningClassifier {

    /// 旧输出（无结构化 code）的文案兜底关键词 —— 与结果页原 isCriticalWarning 的清单一致。
    /// 只在兼容路径使用；新路径不读文案。
    public static let legacyCriticalKeywords: [String] = [
        "不可行", "不可交付", "已回退", "回退到", "回退使用", "回退/不可行",
        "低于目标", "预算已用尽", "预算未用尽",
    ]

    /// 旧输出按文案判严重度：命中关键词 → critical，否则 warning（info 无法从文案区分）。
    public static func legacySeverity(forMessage message: String) -> OptimizationWarningSeverity {
        legacyCriticalKeywords.contains { message.contains($0) } ? .critical : .warning
    }

    /// 分类单条告警。
    /// - 有 code：**只认 code**（未知 code 归 other，记为未按 code 分类）；
    /// - 无 code（旧结果文件）：按既有文案关键词兜底。
    public static func classify(code: String?, message: String) -> OptimizationWarning {
        if let code, let kind = OptimizationWarningKind(rawValue: code) {
            return OptimizationWarning(kind: kind, severity: kind.severity,
                                       code: code, message: message, classifiedByCode: true)
        }
        return OptimizationWarning(kind: .other, severity: legacySeverity(forMessage: message),
                                   code: code, message: message, classifiedByCode: false)
    }
}

public extension OptimizationResult {
    /// 结构化告警列表。
    ///
    /// 新输出（warning_items 非空）走 code 路径；旧结果文件（无该键）走既有文案兜底路径。
    /// 防御：warning_items 为空但 warnings 非空时仍回退文案路径（两者由同一列表派生，
    /// 正常不会出现这种组合）。
    var classifiedWarnings: [OptimizationWarning] {
        if let items = warningItems, !items.isEmpty {
            return items.map { OptimizationWarningClassifier.classify(code: $0.code, message: $0.message) }
        }
        return (warnings ?? []).map { OptimizationWarningClassifier.classify(code: nil, message: $0) }
    }

    /// 是否只有 critical 告警（供结果页决定常显警示条）。
    var hasCriticalWarnings: Bool { classifiedWarnings.contains { $0.severity == .critical } }
}
