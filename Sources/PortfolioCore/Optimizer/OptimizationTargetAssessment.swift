/// 「实际收益 vs 目标下限」的判定结果 —— **容差常量的唯一事实来源**。
///
/// 背景：结果页此前在 View 里用「关键词 + 魔法阈值」判断目标是否达成
/// （`targetGapTolerance = 1e-4` 写在视图里）。优化器一改文案/口径，视图就失配。
/// 这里把语义下沉到 Core：只做判定，不做文案拼装，可单测、可被结果页与敏感性分析共用。
///
/// 口径（与 Optimizer 侧一致）：
/// - 优化目标是「最大化夏普，收益 ≥ 目标」，**目标只是下限、不是锚**。
/// - Python 侧判定「交付收益 ≥ 目标」用 `ret >= target - 1e-4`（阶段一约束与收敛判据），
///   袖内收益下限用 `SLEEVE_FLOOR_EPS = 1e-4` —— 两处同为 1e-4，所以这里必须同量级。
/// - 早先视图里写 0.002（0.2pp）：差额落在 0.02–0.2pp 时告警不触发，
///   「略低于目标」被显示成正常结果（漏报）。
public struct OptimizationTargetAssessment: Equatable, Sendable {

    /// 未达目标的严重度分档。分档依据是**绝对**短缺量（收益率小数，0.001 = 0.1pp），
    /// 不是相对值 —— 目标下限本身可能接近 0，相对值会失真。
    public enum ShortfallSeverity: String, Sendable, CaseIterable {
        /// 已达成：短缺量 ≤ 容差。
        case none
        /// 略微低于目标：短缺量 ≤ 0.1pp。
        case marginal
        /// 低于目标：短缺量 ≤ 0.5pp。
        case material
        /// 明显低于目标：短缺量 > 0.5pp。
        case severe
        /// 入参非有限值（NaN / ±inf），无法判定；按「未达成」处理，不得显示为正常结果。
        case indeterminate

        /// 供 UI 直接使用的中性短标签（长文案仍由视图负责）。
        public var label: String {
            switch self {
            case .none: return "已达成"
            case .marginal: return "略低于目标"
            case .material: return "低于目标"
            case .severe: return "明显低于目标"
            case .indeterminate: return "无法判定"
            }
        }
    }

    /// 判定「交付收益 ≥ 目标下限」的唯一容差：1e-4 = 0.01pp。
    /// **不要在别处再写一份**；视图与其它消费方一律读这里。
    public static let tolerance: Double = 1e-4

    /// 严重度分档上界（绝对短缺量）。
    public static let marginalUpperBound: Double = 1e-3   // 0.1pp
    public static let materialUpperBound: Double = 5e-3   // 0.5pp

    /// 实际交付收益（收益率小数）。
    public let deliveredReturn: Double
    /// 目标下限（收益率小数）。
    public let targetReturn: Double
    /// 实际 − 目标（与结果 JSON 的 `target_gap` 同号同义；不是短缺量）。
    public let gap: Double
    /// 是否达成：`gap >= -tolerance`。
    public let met: Bool
    /// 本次判定使用的容差（恒等于 `OptimizationTargetAssessment.tolerance`）。
    public let tolerance: Double
    /// 低于目标的下限差额 = `max(0, -gap)`；非有限入参时为 0。
    public let shortfall: Double
    /// 严重度分档。
    public let severity: ShortfallSeverity

    /// 高出目标的下限差额 = `max(0, gap)`；未超出时为 0。
    public var excess: Double { gap.isFinite ? max(0, gap) : 0 }

    /// - Parameters:
    ///   - deliveredReturn: 实际交付收益。
    ///   - targetReturn: 目标下限。
    ///   - reportedGap: 结果 JSON 里已给出的 `target_gap`。**非 nil 且有限时优先采用**，
    ///     与视图原来的 `result.targetGap ?? (expectedReturn - target)` 语义一致；
    ///     为 nil 或非有限时回退为 `deliveredReturn - targetReturn`。
    public init(deliveredReturn: Double, targetReturn: Double, reportedGap: Double? = nil) {
        self.deliveredReturn = deliveredReturn
        self.targetReturn = targetReturn
        self.tolerance = OptimizationTargetAssessment.tolerance

        let fallbackGap = deliveredReturn - targetReturn
        let gap: Double
        if let reportedGap, reportedGap.isFinite, deliveredReturn.isFinite, targetReturn.isFinite {
            gap = reportedGap
        } else {
            gap = fallbackGap
        }
        self.gap = gap

        guard gap.isFinite else {
            // NaN / ±inf：证明不了达成，按未达成处理并明确标注，不伪装成正常结果。
            self.met = false
            self.shortfall = 0
            self.severity = .indeterminate
            return
        }

        let met = gap >= -Self.tolerance
        self.met = met
        self.shortfall = met ? 0 : -gap
        self.severity = Self.severity(forShortfall: self.shortfall, met: met)
    }

    /// 严重度分档：容差内 → .none；否则按绝对短缺量逐档上升。
    public static func severity(forShortfall shortfall: Double, met: Bool) -> ShortfallSeverity {
        guard shortfall.isFinite, met == false else {
            // met == true（或短缺量为 0）视为已达成。
            return shortfall.isFinite ? .none : .indeterminate
        }
        if shortfall <= tolerance { return .none }
        if shortfall <= marginalUpperBound { return .marginal }
        if shortfall <= materialUpperBound { return .material }
        return .severe
    }
}

public extension OptimizationResult {
    /// 本结果的目标达成判定；旧结果文件没有 `target_return` → nil（视图自行决定不显示该行）。
    /// 目标取 `targetReturn`，差额优先取结果自带的 `targetGap`，与视图原有口径一致。
    var targetAssessment: OptimizationTargetAssessment? {
        guard let target = targetReturn else { return nil }
        return OptimizationTargetAssessment(deliveredReturn: portfolio.expectedReturn,
                                            targetReturn: target,
                                            reportedGap: targetGap)
    }
}
