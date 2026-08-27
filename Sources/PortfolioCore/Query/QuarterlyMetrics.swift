import Foundation

/// 财务分析逐季度派生指标 (一列 = 一个季度).
/// 公式链 1:1 复刻 投资组合情况.xlsx: 9 个手动字段之外的全部行都由此派生;
/// 未填写的字段按 Excel 空单元格语义参与计算 (= 0), 但派生行在输入不足时显示空白.
public struct QuarterComputed {
    public let report: QuarterlyReport
    /// 0 = 第一列 (期初基准列).
    public let index: Int

    // —— 资产块 ——
    /// 本金 = 总成本 − 累计已实现回报.
    public let principal: Double?
    /// 累计已实现回报 = 累计利息 + 累计股息 + 累计已实现资本利得.
    public let cumRealizedReturn: Double
    /// 累计利息.
    public let cumInterest: Double
    /// 累计股息.
    public let cumDividend: Double
    /// 累计已实现资本利得.
    public let cumRealizedGain: Double
    /// 未实现资本利得 = 总市值 − 总成本.
    public let unrealizedGain: Double?
    /// 累计净总回报 = 累计已实现回报 + 未实现资本利得.
    public let cumNetReturn: Double
    /// 累计净总回报率 (指数口径, 期初 = 1).
    public let cumNetReturnRate: Double?
    /// 报告期天数 (DAYS360 欧洲法, 同 xlsx; 相邻季末恒为 90).
    public let periodDays: Int?

    // —— 现金流块 ——
    /// 当季利息 = 境内 + 境外 (两者皆未填 → nil).
    public let interest: Double?
    /// 利息 YoY% (对比 4 个季度前).
    public let interestYoY: Double?
    /// 当季股息 = 境内 + 境外.
    public let dividend: Double?
    /// 股息 YoY%.
    public let dividendYoY: Double?
    /// 当季资本利得 = 境内 + 境外.
    public let capitalGain: Double?
    /// 资本利得 YoY%.
    public let capitalGainYoY: Double?
    /// 新投资 = 总成本 − 上季总成本 − (红利税、资本利得税).
    public let newInvestment: Double?
    /// 一次投资 = 本金 − 上季本金.
    public let primaryInvestment: Double?
    /// 二次投资 = 新投资 − 一次投资.
    public let secondaryInvestment: Double?
    /// 二次投资 % = 二次投资 / 新投资.
    public let secondaryShare: Double?
    /// 净总回报 (当季) = 累计净总回报 − 上季累计净总回报.
    public let quarterNetReturn: Double
    /// 净总回报 YoY%.
    public let quarterNetReturnYoY: Double?
    /// (股息+利息) / 净总回报.
    public let incomeShare: Double?
    /// 季度收益率 % = 当季净总回报 / 上季总市值.
    public let quarterReturnRate: Double?
    /// 年化 % = 季度收益率 × 4.
    public let annualizedRate: Double?
    /// 均值 = 累计净总回报率 ^ (4 / 经过季度数) − 1 (自成立年化复合回报).
    public let meanRate: Double?
    /// 标准差 = 年化 % 序列的总体标准差 (STDEVP, 自第一个非基准季起).
    public let stdDev: Double?
    /// +95% CI = 均值 + 1.96 × 标准差.
    public let ciPlus: Double?
    /// −95% CI = 均值 − 1.96 × 标准差.
    public let ciMinus: Double?
}

public enum QuarterlyMetrics {

    /// 由手动录入的季度列计算全部派生行. 输入乱序时按 periodEnd 升序计算.
    public static func compute(_ reports: [QuarterlyReport]) -> [QuarterComputed] {
        let sorted = reports.sorted { $0.periodEnd < $1.periodEnd }
        var out: [QuarterComputed] = []
        // 逐列累加的中间量 (nil 输入按 0 累计, 同 Excel 空单元格).
        var cumInterest = 0.0, cumDividend = 0.0, cumRealizedGain = 0.0, cumNetReturn = 0.0
        var cumNetReturnRate: Double? = nil
        var annualizedSeries: [Double] = []   // 自第一个非基准列起的年化 % 序列

        for (k, r) in sorted.enumerated() {
            let interest = sum2(r.interestDomestic, r.interestOverseas)
            let dividend = sum2(r.dividendDomestic, r.dividendOverseas)
            let capitalGain = sum2(r.capitalGainDomestic, r.capitalGainOverseas)

            // 累计链: 上季累计 + 当季 (未填按 0).
            cumInterest += interest ?? 0
            cumDividend += dividend ?? 0
            cumRealizedGain += capitalGain ?? 0
            let cumRealizedReturn = cumInterest + cumDividend + cumRealizedGain

            let principal = r.totalCost.map { $0 - cumRealizedReturn }
            let unrealizedGain: Double? = (r.marketValue == nil || r.totalCost == nil)
                ? nil : r.marketValue! - r.totalCost!
            let prevCumNetReturn = cumNetReturn
            cumNetReturn = cumRealizedReturn + (unrealizedGain ?? 0)
            let quarterNetReturn = cumNetReturn - prevCumNetReturn

            // 报告期: DAYS360 欧洲法 (同 xlsx DAYS360(...,FALSE)).
            let periodDays: Int? = k == 0 ? nil : days360e(sorted[k - 1].periodEnd, r.periodEnd)

            // 季度收益率: 上季总市值为分母; 第一列以总成本为分母 (期初投入).
            var quarterReturnRate: Double?
            if k == 0 {
                if let cost = r.totalCost, cost > 0, quarterNetReturn != 0 {
                    quarterReturnRate = quarterNetReturn / cost
                }
            } else if let prevMV = sorted[k - 1].marketValue, prevMV > 0 {
                quarterReturnRate = quarterNetReturn / prevMV
            }
            let annualizedRate = quarterReturnRate.map { $0 * 4 }

            // 累计净总回报率: 期初 = 1 + 净总回报/总成本; 之后逐季复利.
            if k == 0 {
                cumNetReturnRate = r.totalCost.flatMap { $0 > 0 ? 1 + cumNetReturn / $0 : nil }
            } else if let prevRate = cumNetReturnRate {
                cumNetReturnRate = prevRate * (1 + (quarterReturnRate ?? 0))
            }

            // 均值 (自成立年化复合): 累计净总回报率 ^ (4 / 经过季度数) − 1.
            var meanRate: Double? = nil
            if k >= 1, let rate = cumNetReturnRate, rate > 0 {
                meanRate = pow(rate, 4.0 / Double(k)) - 1
            }

            // 标准差: 年化 % 序列总体标准差 (STDEVP).
            if k >= 1, let ann = annualizedRate { annualizedSeries.append(ann) }
            let stdDev = stdevp(annualizedSeries)
            let ciPlus = combine(meanRate, stdDev) { $0 + 1.96 * $1 }
            let ciMinus = combine(meanRate, stdDev) { $0 - 1.96 * $1 }

            // 新投资块.
            let newInvestment: Double?
            if k >= 1, let cost = r.totalCost, let prevCost = sorted[k - 1].totalCost {
                newInvestment = cost - prevCost - (r.taxes ?? 0)
            } else { newInvestment = nil }
            let primaryInvestment: Double?
            if k >= 1, let p = principal, let prevP = out.last?.principal {
                primaryInvestment = p - prevP
            } else { primaryInvestment = nil }
            let secondaryInvestment = combine(newInvestment, primaryInvestment) { $0 - $1 }
            var secondaryShare: Double? = nil
            if let sec = secondaryInvestment, let inv = newInvestment, inv != 0 {
                secondaryShare = sec / inv
            }

            // YoY% (对比 4 个季度前) 与收益占比.
            let interestYoY = yoy(interest, sorted, k, { sum2($0.interestDomestic, $0.interestOverseas) })
            let dividendYoY = yoy(dividend, sorted, k, { sum2($0.dividendDomestic, $0.dividendOverseas) })
            let capitalGainYoY = yoy(capitalGain, sorted, k, { sum2($0.capitalGainDomestic, $0.capitalGainOverseas) })
            var quarterNetReturnYoY: Double? = nil
            if k >= 4, out[k - 4].quarterNetReturn != 0 {
                quarterNetReturnYoY = quarterNetReturn / out[k - 4].quarterNetReturn - 1
            }
            var incomeShare: Double? = nil
            if (interest != nil || dividend != nil), quarterNetReturn != 0 {
                incomeShare = ((interest ?? 0) + (dividend ?? 0)) / quarterNetReturn
            }

            out.append(QuarterComputed(
                report: r,
                index: k,
                principal: principal,
                cumRealizedReturn: cumRealizedReturn,
                cumInterest: cumInterest,
                cumDividend: cumDividend,
                cumRealizedGain: cumRealizedGain,
                unrealizedGain: unrealizedGain,
                cumNetReturn: cumNetReturn,
                cumNetReturnRate: cumNetReturnRate,
                periodDays: periodDays,
                interest: interest,
                interestYoY: interestYoY,
                dividend: dividend,
                dividendYoY: dividendYoY,
                capitalGain: capitalGain,
                capitalGainYoY: capitalGainYoY,
                newInvestment: newInvestment,
                primaryInvestment: primaryInvestment,
                secondaryInvestment: secondaryInvestment,
                secondaryShare: secondaryShare,
                quarterNetReturn: quarterNetReturn,
                quarterNetReturnYoY: quarterNetReturnYoY,
                incomeShare: incomeShare,
                quarterReturnRate: quarterReturnRate,
                annualizedRate: annualizedRate,
                meanRate: meanRate,
                stdDev: stdDev,
                ciPlus: ciPlus,
                ciMinus: ciMinus))
        }
        return out
    }

    // MARK: - 日期工具

    private static let dayFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        return f
    }()

    /// "2026-06-30" → "2026 Q2".
    public static func quarterLabel(_ periodEnd: String) -> String {
        let parts = periodEnd.split(separator: "-")
        guard parts.count >= 2, let month = Int(parts[1]) else { return periodEnd }
        let q = (month - 1) / 3 + 1
        return "\(parts[0]) Q\(q)"
    }

    /// 下一个季度末 ("2026-06-30" → "2026-09-30").
    public static func nextQuarterEnd(after periodEnd: String) -> String? {
        guard let d = dayFormatter.date(from: periodEnd) else { return nil }
        var comps = Calendar(identifier: .gregorian).dateComponents([.year, .month, .day], from: d)
        comps.day = 1
        comps.month! += 4
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "UTC")!
        guard let firstOfNext = cal.date(from: comps) else { return nil }
        let lastDay = cal.date(byAdding: .day, value: -1, to: firstOfNext)!
        return dayFormatter.string(from: lastDay)
    }

    /// 当前季度的季末日期 (首列默认值).
    public static func currentQuarterEnd() -> String {
        let now = Date()
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "UTC")!
        var comps = cal.dateComponents([.year, .month, .day], from: now)
        comps.day = 1
        comps.month = (comps.month! - 1) / 3 * 3 + 3   // 本季度最后一个月
        guard let quarterLastMonth = cal.date(from: comps) else { return dayFormatter.string(from: now) }
        let end = cal.date(byAdding: .day, value: -1, to: quarterLastMonth) ?? now
        return dayFormatter.string(from: end)
    }

    // MARK: - 私有工具

    /// 两个可空分量求和: 两者皆空 → nil; 否则空分量按 0 (Excel SUM 语义).
    private static func sum2(_ a: Double?, _ b: Double?) -> Double? {
        if a == nil && b == nil { return nil }
        return (a ?? 0) + (b ?? 0)
    }

    /// 两个可空值合并; 闭包返回 nil 时整体为 nil.
    private static func combine<A, B, C>(_ a: A?, _ b: B?, _ f: (A, B) -> C?) -> C? {
        guard let a, let b else { return nil }
        return f(a, b)
    }

    private static func yoy(_ current: Double?, _ sorted: [QuarterlyReport], _ k: Int,
                            _ value: (QuarterlyReport) -> Double?) -> Double? {
        guard k >= 4, let cur = current else { return nil }
        guard let past = value(sorted[k - 4]), past != 0 else { return nil }
        return cur / past - 1
    }

    /// 总体标准差 (Excel STDEVP); 空序列 → nil, 单值 → 0.
    private static func stdevp(_ values: [Double]) -> Double? {
        if values.isEmpty { return nil }
        if values.count == 1 { return 0 }
        let mean = values.reduce(0, +) / Double(values.count)
        let variance = values.reduce(0) { $0 + ($1 - mean) * ($1 - mean) } / Double(values.count)
        return variance.squareRoot()
    }

    /// DAYS360 欧洲法 (Excel DAYS360(start, end, FALSE)): 31 日按 30 计.
    private static func days360e(_ start: String, _ end: String) -> Int? {
        func parts(_ s: String) -> (Int, Int, Int)? {
            let p = s.split(separator: "-").compactMap { Int($0) }
            guard p.count == 3 else { return nil }
            return (p[0], p[1], p[2])
        }
        guard let (y1, m1, d1) = parts(start), let (y2, m2, d2) = parts(end) else { return nil }
        let dd1 = min(d1, 30), dd2 = min(d2, 30)
        return (y2 - y1) * 360 + (m2 - m1) * 30 + (dd2 - dd1)
    }
}
