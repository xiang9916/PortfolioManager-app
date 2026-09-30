import Foundation

/// 财务分析「总市值 / 总成本」两格的一键填入规则。
///
/// 抽成纯逻辑放在 Core 里，是为了让这条口径能被单测锁住 —— 它决定了往用户的历史底稿里
/// 写什么数，而视图层无法在 CI 里点击验证。
///
/// - 只有这两个字段可填；其余 7 个是期间现金流，没有「当前值」这个概念。
/// - 期初基准列（最早的季度）语义是「起点：总市值 = 总成本」，两格都填**总成本**。
/// - 返回 `nil` = 现在没有可填的数（例如行情未抓取导致总市值为 0）→ 按钮不出现。
///
/// ⚠️ 填入的是**当下**的实时值，不是该季末的历史值：App 不记录历史总市值
/// （`snapshots` 表存在但从未被写入），没有别的选择。见 `docs/adr/0001`。
public enum QuarterlyFill {
    public static func value(field: QuarterlyField,
                             periodEnd: String,
                             reports: [QuarterlyReport],
                             allocation: AllocationSnapshot?) -> Double? {
        guard field == .marketValue || field == .totalCost, let alloc = allocation else { return nil }
        if periodEnd == reports.map(\.periodEnd).min() {
            return alloc.totalCost > 0 ? alloc.totalCost : nil
        }
        let v = (field == .marketValue) ? alloc.totalValue : alloc.totalCost
        return v > 0 ? v : nil
    }
}
