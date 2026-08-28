import Foundation

public enum Pool: String, Codable, Hashable {
    case domestic, overseas, cross
}

public struct Asset: Codable, Identifiable, Hashable {
    public var id: Int64?
    public let key: String
    public let name: String
    public let ticker: String?
    public let market: String?
    public let assetClass: String?
    public let pool: Pool
    public let currency: String
    public let source: String?
    public let feeRate: Double?
    /// 手动排序序号 (资产透视拖动排序); nil = 尚未参与排序.
    public let sortOrder: Double?

    public init(id: Int64? = nil, key: String, name: String, ticker: String? = nil,
                market: String? = nil, assetClass: String? = nil, pool: Pool,
                currency: String = "CNY", source: String? = nil, feeRate: Double? = nil,
                sortOrder: Double? = nil) {
        self.id = id; self.key = key; self.name = name; self.ticker = ticker
        self.market = market; self.assetClass = assetClass; self.pool = pool
        self.currency = currency; self.source = source; self.feeRate = feeRate
        self.sortOrder = sortOrder
    }
}

public struct Holding: Codable, Identifiable, Hashable {
    public var id: Int64?
    public let assetKey: String
    /// 份额/数量 (shares / units). 市值 = 份额 × 最后价 (派生值, 不存储).
    public let quantity: Double
    /// 成本/本金, denominated in `currency` (the asset's own currency).
    public let costBasis: Double
    /// ISO currency code of `costBasis` and the derived market value (e.g. "USD", "CNY").
    public let currency: String
    public let asOfDate: String

    public init(id: Int64? = nil, assetKey: String, quantity: Double,
                costBasis: Double, currency: String = "CNY", asOfDate: String) {
        self.id = id; self.assetKey = assetKey; self.quantity = quantity
        self.costBasis = costBasis; self.currency = currency
        self.asOfDate = asOfDate
    }
}

/// A CNY exchange rate for one currency (能力2 权重汇率统一).
public struct FxRate: Codable, Identifiable, Hashable {
    public let currency: String
    public let rateToCny: Double
    public let asOfDate: String
    public let source: String?
    public var id: String { currency }

    public init(currency: String, rateToCny: Double, asOfDate: String, source: String? = nil) {
        self.currency = currency; self.rateToCny = rateToCny
        self.asOfDate = asOfDate; self.source = source
    }
}

public struct Snapshot: Codable, Identifiable, Hashable {
    public var id: Int64?
    public let date: String
    public let totalValue: Double
    public let domesticValue: Double
    public let overseasValue: Double

    public init(id: Int64? = nil, date: String, totalValue: Double,
                domesticValue: Double, overseasValue: Double) {
        self.id = id; self.date = date; self.totalValue = totalValue
        self.domesticValue = domesticValue; self.overseasValue = overseasValue
    }
}

public struct PricePoint: Codable, Identifiable, Hashable {
    public var id: Int64?
    public let assetKey: String
    public let date: String
    public let close: Double
    public let currency: String

    public init(id: Int64? = nil, assetKey: String, date: String,
                close: Double, currency: String = "USD") {
        self.id = id; self.assetKey = assetKey; self.date = date
        self.close = close; self.currency = currency
    }
}

public enum FinancialPeriod: String, Codable, CaseIterable { case quarter, halfYear, annual }

/// 能力4 财务分析: 个人收益结构的期间汇总 (股息分红 + 交易损益), 组合级(非标的级).
public struct IncomeSummary: Codable, Identifiable, Hashable {
    public var id: Int64?
    public let period: FinancialPeriod
    public let periodEnd: String
    /// 股息分红 (人民币).
    public let dividends: Double
    /// 交易损益 (已实现, 人民币).
    public let realizedPnl: Double
    public let source: String?

    public init(id: Int64? = nil, period: FinancialPeriod, periodEnd: String,
                dividends: Double = 0, realizedPnl: Double = 0, source: String? = nil) {
        self.id = id; self.period = period; self.periodEnd = periodEnd
        self.dividends = dividends; self.realizedPnl = realizedPnl; self.source = source
    }
}

/// 能力4 重做: 财务分析逐季度报表的一列 (对应 投资组合情况.xlsx 的一个季度列).
/// 9 个字段全部手动录入 (季度结束后补录), 录入后仍可编辑; 其余行由 QuarterlyMetrics 派生.
/// nil = 尚未填写 (网格显示空白, 计算按 0 处理, 同 Excel 空单元格语义).
public struct QuarterlyReport: Codable, Identifiable, Hashable {
    public var id: Int64?
    /// 季度截止日 "yyyy-MM-dd" (如 2026-06-30), 一列一个季度.
    public var periodEnd: String
    /// 总市值 (季度末组合市值, 人民币).
    public var marketValue: Double?
    /// 总成本 (含已实现回报滚存的成本口径, 人民币).
    public var totalCost: Double?
    /// 当季利息 · 境内.
    public var interestDomestic: Double?
    /// 当季利息 · 境外.
    public var interestOverseas: Double?
    /// 当季股息 · 境内.
    public var dividendDomestic: Double?
    /// 当季股息 · 境外.
    public var dividendOverseas: Double?
    /// 当季资本利得 · 境内.
    public var capitalGainDomestic: Double?
    /// 当季资本利得 · 境外.
    public var capitalGainOverseas: Double?
    /// 当季 (红利税、资本利得税) 合计.
    public var taxes: Double?
    public var source: String?

    public init(id: Int64? = nil, periodEnd: String,
                marketValue: Double? = nil, totalCost: Double? = nil,
                interestDomestic: Double? = nil, interestOverseas: Double? = nil,
                dividendDomestic: Double? = nil, dividendOverseas: Double? = nil,
                capitalGainDomestic: Double? = nil, capitalGainOverseas: Double? = nil,
                taxes: Double? = nil, source: String? = nil) {
        self.id = id; self.periodEnd = periodEnd
        self.marketValue = marketValue; self.totalCost = totalCost
        self.interestDomestic = interestDomestic; self.interestOverseas = interestOverseas
        self.dividendDomestic = dividendDomestic; self.dividendOverseas = dividendOverseas
        self.capitalGainDomestic = capitalGainDomestic; self.capitalGainOverseas = capitalGainOverseas
        self.taxes = taxes; self.source = source
    }
}

/// 9 个手动字段之一 (编辑网格用).
public enum QuarterlyField: String, CaseIterable, Codable {
    case marketValue, totalCost
    case interestDomestic, interestOverseas
    case dividendDomestic, dividendOverseas
    case capitalGainDomestic, capitalGainOverseas
    case taxes

    /// 网格左侧行标签.
    public var label: String {
        switch self {
        case .marketValue: return "总市值"
        case .totalCost: return "总成本"
        case .interestDomestic: return "境内"
        case .interestOverseas: return "境外"
        case .dividendDomestic: return "境内"
        case .dividendOverseas: return "境外"
        case .capitalGainDomestic: return "境内"
        case .capitalGainOverseas: return "境外"
        case .taxes: return "(红利税、资本利得税)"
        }
    }

    public var keyPath: WritableKeyPath<QuarterlyReport, Double?> {
        switch self {
        case .marketValue: return \.marketValue
        case .totalCost: return \.totalCost
        case .interestDomestic: return \.interestDomestic
        case .interestOverseas: return \.interestOverseas
        case .dividendDomestic: return \.dividendDomestic
        case .dividendOverseas: return \.dividendOverseas
        case .capitalGainDomestic: return \.capitalGainDomestic
        case .capitalGainOverseas: return \.capitalGainOverseas
        case .taxes: return \.taxes
        }
    }
}

public struct OptimizedAsset: Codable, Hashable {
    public let key: String
    public let name: String
    public let weight: Double
    public let expectedReturn: Double
    public let volatility: Double
    public let sharpe: Double
}

public struct PortfolioSummary: Codable, Hashable {
    public let expectedReturn: Double
    public let volatility: Double
    public let sharpe: Double
    public let worstYear95: Double
}

public struct Benchmark: Codable, Hashable {
    public let expectedReturn: Double
    public let volatility: Double
    public let sharpe: Double
    public let worstYear95: Double
    public let portfolioAlpha: Double
    public let portfolioBeta: Double
    public let volatilityReduction: Double
    public let worstYearImprovement: Double
    public let weights: [String: Double]?
}

/// 新标的测试: 单个测试标的的估算参数 (Python 侧 test_assets 透传)。
public struct TestAssetEstimate: Codable, Hashable {
    public let key: String
    public let ticker: String
    public let source: String
    public let pool: String
    public let mu: Double
    public let vol: Double
    public let nDays: Int
}

public struct OptimizationResult: Codable {
    public let generatedAt: String?
    public let portfolio: PortfolioSummary
    public let assets: [OptimizedAsset]
    public let benchmark: Benchmark?
    public let testAssets: [TestAssetEstimate]?
    public let sourceDetail: String?
}

public struct OptimizationRun: Codable, Identifiable {
    public var id: Int64?
    public let startedAt: String
    public var finishedAt: String?
    public var status: String
    public let paramsHash: String?
    public var resultJSON: String?
    public var logPath: String?

    public init(id: Int64? = nil, startedAt: String, finishedAt: String? = nil,
                status: String = "pending", paramsHash: String? = nil,
                resultJSON: String? = nil, logPath: String? = nil) {
        self.id = id; self.startedAt = startedAt; self.finishedAt = finishedAt
        self.status = status; self.paramsHash = paramsHash
        self.resultJSON = resultJSON; self.logPath = logPath
    }
}

public struct OptimizationLogEntry: Codable, Identifiable {
    public var id: Int64?
    public let runID: Int64
    public let seq: Int
    public let step: String
    public let message: String
    public let level: String
    public let ts: String
}

// MARK: - Sensitivity Analysis

public struct SensitivityPoint: Codable, Hashable, Identifiable {
    public var id: Int { Int((targetReturn * 10000).rounded()) }
    public let targetReturn: Double
    public let feasible: Bool
    public let achievedReturn: Double?
    public let volatility: Double?
    public let sharpe: Double?
    public let worstYear95: Double?
    /// 对数正态精确 CAGR：exp(E[ln(1+R)])−1（矩匹配）
    public let cagrLognormal: Double?
    /// 肥尾混合 CAGR：亏损侧 Student-t(α=ν) 幂律尾（下限 −99.9%）+ 盈利侧对数正态拼接
    public let cagrFatTail: Double?
}

public struct SensitivityAnalysisResult: Codable {
    public let maxFeasibleReturn: Double
    /// 肥尾模型自由度（尾部指数 α），对应 Python 端 --tail-dof
    public let tailDof: Double?
    public let rf: Double
    public let domesticWeight: Double
    public let overseasWeight: Double
    public let points: [SensitivityPoint]
}
