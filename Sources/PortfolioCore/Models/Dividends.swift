import Foundation

// MARK: - 股息 (全资产股息 / 全资产股息率)

/// 数据源返回的一笔每股/每份现金股息 (税前, 标的报价币种).
/// Yahoo `events=div` 与 Eastmoney 基金分红解析后统一成这个形状.
public struct DividendEvent: Codable, Hashable, Sendable {
    /// 除息日 "yyyy-MM-dd" — 归入自然年的唯一依据 (决策 Q18).
    public let exDate: String
    /// 每股/每份现金股息 (税前, 报价币种). 已含特别股息, 不含送股 (决策 Q21).
    public let amount: Double
    /// 报价币种 (Yahoo 会把跨币种派息折算进报价币种 — 决策 Q27).
    public let currency: String

    public init(exDate: String, amount: Double, currency: String) {
        self.exDate = exDate
        self.amount = amount
        self.currency = currency
    }
}

/// 落库的一笔股息 (标的级). 主键 = (asset_key, ex_date).
public struct DividendRecord: Codable, Hashable, Identifiable {
    public let assetKey: String
    public let exDate: String
    public let amount: Double
    public let currency: String
    public let source: String

    public var id: String { assetKey + "@" + exDate }

    public init(assetKey: String, exDate: String, amount: Double,
                currency: String, source: String) {
        self.assetKey = assetKey
        self.exDate = exDate
        self.amount = amount
        self.currency = currency
        self.source = source
    }

    /// 除息日所在自然年 (决策 Q18: 按除息日归属).
    public var year: Int { Int(exDate.prefix(4)) ?? 0 }
}

/// 抓取状态 — 支撑「0 / NULL / 有值」三态 (决策 Q28).
///
/// - `ok`: 抓取成功。该标的本就无分红时记录为空, 显示 `¥0.00` (确认无分红)。
/// - `unavailable`: 数据源对该标的没有历史覆盖 (如新上市标的
///   `1111.HK` / `2222.HK` 返回 `Data doesn't exist`), 显示 `—`。
/// - `failed`: 网络/解析失败, 显示 `—`; 已抓到的历史记录保留不删。
///
/// 汇总计算时 `unavailable` / `failed` 一律按 0 计入 (决策 Q28), 但覆盖率
/// 提示只统计它们 (决策 Q12)。
public enum DividendFetchStatus: String, Codable, Hashable, Sendable {
    case ok
    case unavailable
    case failed
}

/// 单个标的的股息抓取元数据 (缓存新鲜度 + 详情里的来源/时间展示 — 决策 Q32).
public struct DividendFetchMeta: Codable, Hashable, Identifiable {
    public let assetKey: String
    public let status: DividendFetchStatus
    public let source: String?
    /// "yyyy-MM-dd HH:mm:ss" (本地时区) — 7 天缓存 + 同年判断 (决策 Q24/Q26).
    public let fetchedAt: String

    public var id: String { assetKey }

    public init(assetKey: String, status: DividendFetchStatus,
                source: String?, fetchedAt: String) {
        self.assetKey = assetKey
        self.status = status
        self.source = source
        self.fetchedAt = fetchedAt
    }
}

// MARK: - 股息税 (决策 Q4 / Q36 / Q38)

/// 按市场固定税率。判定顺序: `assets.market` 优先, 为空/未识别则回退 `currency`。
/// 两者都无法判定 → nil (未配置, 按 0% 计并在详情标注)。
///
/// 税率表 (用户确认): 美股 10% / 港股 28% / A股 0%。
public enum DividendTax {
    public static let usRate = 0.10
    public static let hkRate = 0.28
    public static let cnRate = 0.0

    private static let usMarkets: Set<String> = [
        "US", "USA", "U.S.", "U.S", "NASDAQ", "NYSE", "AMEX", "ARCA", "BATS",
        "US_MARKET", "UNITED_STATES",
    ]
    private static let hkMarkets: Set<String> = [
        "HK", "HKEX", "SEHK", "HONGKONG", "HONG_KONG", "港股",
    ]
    private static let cnMarkets: Set<String> = [
        "CN", "SH", "SZ", "SS", "SSE", "SZSE", "CHINA", "A", "ASHARE",
        "A_SHARE", "A股", "中国",
    ]

    /// nil = 未配置税率 (调用方按 0 处理并标注)。
    public static func rate(market: String?, currency: String) -> Double? {
        if let raw = market?.trimmingCharacters(in: .whitespacesAndNewlines), !raw.isEmpty {
            let m = raw.uppercased()
            if usMarkets.contains(m) || m.contains("NASDAQ") || m.contains("NYSE")
                || m.contains("AMEX") {
                return usRate
            }
            if hkMarkets.contains(m) || m.contains("HKEX") || m.contains("SEHK") {
                return hkRate
            }
            if cnMarkets.contains(m) || m.contains("SHANGHAI") || m.contains("SHENZHEN") {
                return cnRate
            }
        }
        switch currency.uppercased() {
        case "USD": return usRate
        case "HKD": return hkRate
        case "CNY": return cnRate
        default: return nil
        }
    }

    /// 描述用 (详情 tooltip): "未配置" / "10%" ...
    public static func label(_ rate: Double?) -> String {
        guard let rate else { return "未配置" }
        return String(format: "%.0f%%", rate * 100)
    }
}

// MARK: - 单标的股息展示块 (资产透视详情)

/// 一个资产的股息派生结果。`prevPrevTotal` / `prevTotal` / `estimatedTotal` /
/// `estimatedTotalCny` 为 nil 时表示 NULL 态 (详情显示「—」)。
public struct AssetDividend: Codable, Hashable {
    public let status: DividendFetchStatus
    /// nil = 尚未抓取过 (与 unavailable 一样按 NULL 态展示).
    public let source: String?
    public let fetchedAt: String?

    public let yearPrevPrev: Int
    public let yearPrev: Int
    public let yearCurrent: Int

    /// 每股/每份股息合计 (原币, 税前)。
    public let perSharePrevPrev: Double
    public let perSharePrev: Double

    /// 前年股息 = 前年每股股息 × 份额 (原币, 税前)。
    public let prevPrevTotal: Double?
    /// 去年股息 = 去年每股股息 × 份额 (原币, 税前)。
    public let prevTotal: Double?
    /// 今年预计股息 (原币, 税前) = max(0, 2×去年 − 前年) × 份额 (决策 Q5: 负值截断为 0)。
    public let estimatedTotal: Double?
    /// 今年预计股息 (折人民币, 税后) — 决策 Q35: 原币税前、折人民币税后。
    public let estimatedTotalCny: Double?
    /// 今年已发生股息 (折人民币, 税后, 截至今日) — 决策 Q30。
    public let actualYtdCny: Double?

    /// 税率 (nil = 未配置, 按 0 计)。
    public let taxRate: Double?
    /// 汇率缺失 → 折人民币为 nil (决策 Q14: 不静默按 1.0)。
    public let fxMissing: Bool

    public let quantity: Double
    public let currency: String

    public init(status: DividendFetchStatus, source: String?, fetchedAt: String?,
                yearPrevPrev: Int, yearPrev: Int, yearCurrent: Int,
                perSharePrevPrev: Double, perSharePrev: Double,
                prevPrevTotal: Double?, prevTotal: Double?,
                estimatedTotal: Double?, estimatedTotalCny: Double?,
                actualYtdCny: Double?, taxRate: Double?, fxMissing: Bool,
                quantity: Double, currency: String) {
        self.status = status; self.source = source; self.fetchedAt = fetchedAt
        self.yearPrevPrev = yearPrevPrev; self.yearPrev = yearPrev; self.yearCurrent = yearCurrent
        self.perSharePrevPrev = perSharePrevPrev; self.perSharePrev = perSharePrev
        self.prevPrevTotal = prevPrevTotal; self.prevTotal = prevTotal
        self.estimatedTotal = estimatedTotal; self.estimatedTotalCny = estimatedTotalCny
        self.actualYtdCny = actualYtdCny; self.taxRate = taxRate
        self.fxMissing = fxMissing; self.quantity = quantity; self.currency = currency
    }
}

/// 全资产股息聚合结果 (资产管理界面指标卡 — 决策 Q9/Q12)。
public struct DividendSummary: Codable, Hashable {
    /// 全资产股息 = Σ 各资产今年预计股息 (税后, 折人民币); NULL 态按 0 计入。
    public let totalNetCny: Double
    /// 有覆盖 (非 NULL 态) 的资产数 — tooltip 「覆盖 N/M」。
    public let coveredCount: Int
    public let assetCount: Int
    /// 最近一次抓取时间 (取最大 fetchedAt), nil = 从未抓取。
    public let fetchedAt: String?

    public init(totalNetCny: Double, coveredCount: Int, assetCount: Int, fetchedAt: String?) {
        self.totalNetCny = totalNetCny
        self.coveredCount = coveredCount
        self.assetCount = assetCount
        self.fetchedAt = fetchedAt
    }
}
