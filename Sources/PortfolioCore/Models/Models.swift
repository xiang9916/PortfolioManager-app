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

    public init(id: Int64? = nil, key: String, name: String, ticker: String? = nil,
                market: String? = nil, assetClass: String? = nil, pool: Pool,
                currency: String = "CNY", source: String? = nil, feeRate: Double? = nil) {
        self.id = id; self.key = key; self.name = name; self.ticker = ticker
        self.market = market; self.assetClass = assetClass; self.pool = pool
        self.currency = currency; self.source = source; self.feeRate = feeRate
    }
}

public struct Holding: Codable, Identifiable, Hashable {
    public var id: Int64?
    public let assetKey: String
    public let quantity: Double
    public let costBasis: Double
    public let valueCny: Double
    public let asOfDate: String

    public init(id: Int64? = nil, assetKey: String, quantity: Double,
                costBasis: Double, valueCny: Double, asOfDate: String) {
        self.id = id; self.assetKey = assetKey; self.quantity = quantity
        self.costBasis = costBasis; self.valueCny = valueCny; self.asOfDate = asOfDate
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

public enum FinancialPeriod: String, Codable { case quarter, halfYear, annual }

public struct Financial: Codable, Identifiable, Hashable {
    public var id: Int64?
    public let assetKey: String
    public let period: FinancialPeriod
    public let periodEnd: String
    public let revenue: Double?
    public let netIncome: Double?
    public let eps: Double?
    public let source: String?

    public init(id: Int64? = nil, assetKey: String, period: FinancialPeriod,
                periodEnd: String, revenue: Double? = nil, netIncome: Double? = nil,
                eps: Double? = nil, source: String? = nil) {
        self.id = id; self.assetKey = assetKey; self.period = period
        self.periodEnd = periodEnd; self.revenue = revenue
        self.netIncome = netIncome; self.eps = eps; self.source = source
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

public struct OptimizationResult: Codable {
    public let generatedAt: String?
    public let portfolio: PortfolioSummary
    public let assets: [OptimizedAsset]
    public let benchmark: Benchmark?
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
