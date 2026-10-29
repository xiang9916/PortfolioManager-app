import Foundation

/// 涨跌归因的时间窗口。
///
/// 窗口 = 合成日 K 的**最后 N 根**（含期初那一根），所以「当日」= 最后 2 根
/// = 1 个交易日的涨跌；「近 5 日」= 6 根；「近 20 日」= 20 根（可用不足时按实际根数）。
/// 口径见 ADR 0013。
public enum FocusWindow: String, CaseIterable, Identifiable, Sendable {
    case day
    case week
    case month

    public var id: String { rawValue }

    /// 窗口包含的合成 K 根数（含期初那一根）。
    public var tradingBars: Int {
        switch self {
        case .day: return 2
        case .week: return 6
        case .month: return 20
        }
    }

    /// 窗口覆盖的交易日数（= 根数 − 1），文案用。
    public var tradingDays: Int { tradingBars - 1 }

    /// 界面标签（与「当日涨幅最高」的极值卡区分：这里区间是**当日/近 5 日/近 20 日**）。
    public var label: String {
        switch self {
        case .day: return "当日"
        case .week: return "近 5 日"
        case .month: return "近 20 日"
        }
    }
}

/// 一只标的在某个窗口里的归因结果。
///
/// - `weight` = **当前**市值权重（折人民币、归一后的展示值）；
/// - `contribution` = **期初有效权重 × 区间自身涨跌**，单位是组合涨跌幅的小数（乘 100 = pp）。
///   期初有效权重与当前权重不同：区间内涨得多的标的，期初占比更小。
public struct FocusContribution: Hashable, Identifiable, Sendable {
    public let assetKey: String
    public let name: String
    /// 该标的自己的区间涨跌 = 今收 ÷ 期初收盘 − 1（窗口日历口径，前向填充）。
    public let change: Double
    /// 当前市值权重（同一窗口内参与者之间归一，合计 1）。
    public let weight: Double
    /// 对该窗口组合涨跌的贡献（小数；正 = 拉动上涨，负 = 拖累）。
    public let contribution: Double
    /// 窗口内没有新价（期初收盘 = 今收）→ 涨跌按 0% 计，视图标「无新价」。
    public let stale: Bool
    public var id: String { assetKey }

    public init(assetKey: String, name: String, change: Double, weight: Double,
                contribution: Double, stale: Bool) {
        self.assetKey = assetKey
        self.name = name
        self.change = change
        self.weight = weight
        self.contribution = contribution
        self.stale = stale
    }
}

/// 一个窗口的完整归因：组合区间涨跌 + 逐标的贡献。
///
/// 恒等式（ADR 0013）：`Σ contributions[].contribution == totalChange`（浮点 1e-12 内）。
/// 期初之后才首次出现价格的标的不在期初参与集里、不贡献 —— 恒等式仍然成立。
public struct FocusAttribution: Hashable, Sendable {
    public let window: FocusWindow
    /// 期初 / 期末的合成日历日期（"yyyy-MM-dd"）。
    public let startDate: String
    public let endDate: String
    /// 组合区间涨跌 = 期末合成收盘 ÷ 期初合成收盘 − 1。
    public let totalChange: Double
    /// 上涨贡献合计（≥ 0）与下跌贡献合计（≤ 0），单位同 `totalChange`。
    public let upside: Double
    public let downside: Double
    public let gainers: Int
    public let losers: Int
    /// 按贡献降序（并列按 assetKey 稳定排序）。
    public let contributions: [FocusContribution]
    /// 前 3 大 |贡献| 占全部 |贡献| 的比例（集中度）；全部贡献为 0 时 nil。
    public let top3Share: Double?

    public init(window: FocusWindow, startDate: String, endDate: String,
                totalChange: Double, upside: Double, downside: Double,
                gainers: Int, losers: Int, contributions: [FocusContribution],
                top3Share: Double?) {
        self.window = window
        self.startDate = startDate
        self.endDate = endDate
        self.totalChange = totalChange
        self.upside = upside
        self.downside = downside
        self.gainers = gainers
        self.losers = losers
        self.contributions = contributions
        self.top3Share = top3Share
    }

    public func contribution(for assetKey: String) -> FocusContribution? {
        contributions.first { $0.assetKey == assetKey }
    }
}
