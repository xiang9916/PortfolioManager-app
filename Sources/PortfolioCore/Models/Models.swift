/// 资金池归属 —— 只有两种。池是**派生量**：由 `AssetMarket` 解析出的市场推导
/// （中国内地 → 境内，其余 → 境外；市场解析不出来时按币种回退，CNY → 境内）。
/// 旧的第三态 `cross` 已废除，见 `docs/adr/0002-remove-cross-pool.md`。
public enum Pool: String, Codable, Hashable, Sendable {
    case domestic, overseas
}

public struct Asset: Codable, Identifiable, Hashable {
    public var id: Int64?
    public let key: String
    public let name: String
    public let ticker: String?
    /// 标的所属市场（闭集，见 `AssetMarket`）。`nil` = 未设置，读时按 ticker / 币种回退。
    public let market: AssetMarket?
    public let assetClass: String?
    /// 池归属的**落库缓存**。UI 与统计一律经 `AssetMarket.pool(...)` 读时派生（Q31），
    /// 这里只保证备份/导出与 pm-cli 拿到一个一致的快照。
    public let pool: Pool
    public let currency: String
    public let source: String?
    public let feeRate: Double?
    /// 手动排序序号 (资产明细拖动排序); nil = 尚未参与排序.
    public let sortOrder: Double?

    public init(id: Int64? = nil, key: String, name: String, ticker: String? = nil,
                market: AssetMarket? = nil, assetClass: String? = nil, pool: Pool,
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
    /// 成本, denominated in `currency` (the asset's own currency).
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

public struct PricePoint: Codable, Identifiable, Hashable, Sendable {
    public var id: Int64?
    public let assetKey: String
    public let date: String
    public let close: Double
    /// **真实现价** —— 权益 = 未复权收盘价；基金 = 单位净值（份额净值）。
    ///
    /// `close` 是总回报口径（Yahoo `adjclose` / 东财累计净值，ADR 0005），不等于现实世界的价格：
    /// 基金累计净值可能是份额净值的好几倍，权益在分红窗口里也被复权因子压低。
    /// nil = 该行未回补（v10 之前抓的历史行；下次「更新行情」会补齐）。
    public let rawClose: Double?
    public let currency: String
    /// 当日开 / 高 / 低 —— **与 close 同口径（复权）**。nil = 数据源没有日内价。
    ///
    /// 基金（东财净值）永远为 nil；v9 之前抓的权益历史也为 nil（下次「更新行情」回补）。
    /// 两种缺失都按同一规则降级：开 = 高 = 低 = 昨收、收 = 今收（见 ADR 0008）。
    public let open: Double?
    public let high: Double?
    public let low: Double?

    public init(id: Int64? = nil, assetKey: String, date: String,
                close: Double, rawClose: Double? = nil, currency: String = "USD",
                open: Double? = nil, high: Double? = nil, low: Double? = nil) {
        self.id = id; self.assetKey = assetKey; self.date = date
        self.close = close; self.rawClose = rawClose; self.currency = currency
        self.open = open; self.high = high; self.low = low
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

/// 能力4 重做: 财务分析逐季度报表的一列 (对应 portfolio.xlsx 的一个季度列).
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
    /// 「新标的测试」临时注入的标的 (key 以 T_ 开头)。这类行是 what-if 口径,
    /// 不进执行清单, 界面上单独着色。普通优化结果为 nil/false。
    public let isTest: Bool?
    /// 「持仓纳入」注入的持仓标的 (key 以 H_ 开头): 持仓里不在候选资产目录内的标的,
    /// 按 what-if 口径加入候选池。与 isTest 一样不进执行清单; 旧结果文件没有该键 → nil。
    public let isHeld: Bool?
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
    /// 测试标的的类别：broad（宽基/大类）/ strategy（策略指数）/ active（个股、主动）。
    /// 类别决定 μ 的构造与历史窗口（旧结果文件无此字段 → nil）。
    public let testClass: String?
    public let windowYears: Int?
    /// μ 的口径备注：history_only（无先验，退化为纯历史）/ prior_fallback / fixed。
    public let muNote: String?
    /// σ 的口径备注：history（历史年化波动，已套统一护栏 1%~60%）。
    public let volNote: String?
    public let muHist: Double?
    public let volHist: Double?
}

/// 持仓纳入: 一只**不在候选资产目录内**的持仓被注入为 what-if 资产后的估算参数。
///
/// 对应结果 summary 的 `held_assets.included`（Python 侧由 `held_assets.py` 注入后透传）。
/// 字段**全部 optional**: 旧结果文件没有这个块 → nil；Python 侧是加法式输出，
/// 缺字段不应让整条结果解码失败。
public struct HeldAssetEstimate: Codable, Hashable {
    /// 标的代码（6 位境内基金代码会被归一到裸代码）。
    public let ticker: String?
    /// 展示名（Python 侧目前通常不带，界面回退到 ticker）。
    public let name: String?
    /// 预期收益 μ（what-if 口径：active 类 = 纯历史年化）。
    public let mu: Double?
    /// 历史年化波动 σ（已套统一护栏 1%~60%）。
    public let vol: Double?
    /// 历史样本天数。
    public let nDays: Int?
    /// 池归属：`domestic` / `overseas`。
    public let pool: String?
    /// 类别：broad / strategy / active（持仓注入固定 active）。
    public let testClass: String?
    /// μ 的历史窗口年数（nil = 全历史）。
    public let windowYears: Int?
    /// μ 的口径备注（history_only / prior_fallback / fixed）。
    public let muNote: String?
    /// σ 的口径备注（history）。
    public let volNote: String?
    /// 该估算来自持仓注入（Python 侧透传，正常为 true）。
    public let isHeld: Bool?
}

/// 持仓纳入: 因数据不可用 / 已在候选池等原因未被注入的持仓。
public struct HeldAssetSkip: Codable, Hashable {
    public let ticker: String?
    public let reason: String?
}

/// 结果 summary 的 `held_assets` 块（持仓纳入的结论）。
///
/// 旧结果文件没有该键 → `OptimizationResult.heldAssets` 为 nil，界面整个区不显示。
public struct HeldAssetsInfo: Codable, Hashable {
    public let included: [HeldAssetEstimate]?
    public let skipped: [HeldAssetSkip]?
}

public struct OptimizationResult: Codable {
    public let generatedAt: String?
    public let portfolio: PortfolioSummary
    public let assets: [OptimizedAsset]
    public let benchmark: Benchmark?
    public let testAssets: [TestAssetEstimate]?
    /// 汇总阶段的自检/管线提示（结果清单是否覆盖了全部权重、基金剔除原因等）。
    /// 旧结果文件没有这个键 → nil。
    public let warnings: [String]?
    /// 目标收益率（优化目标是最大化夏普，它只是收益下限）与实际收益超出目标的幅度。
    public let targetReturn: Double?
    public let targetGap: Double?
    /// 加法式结构化告警（每条带稳定 code，见 Optimizer/scripts/optimize_portfolio.py 的 WARNING_CODES）。
    /// 旧结果文件没有这个键 → nil；分类走 OptimizationWarningClassifier 的文案兜底路径。
    public let warningItems: [OptimizationWarningItem]?
    public let objective: String?
    public let sourceDetail: String?
    /// 袖内参数 {袖 key: [μ, σ]}（summary 键 `sleeve_params`）。
    /// 敏感性分析把它作为「锚定口径」的候选初值，让锚点目标上的点与主结果同源。
    /// 旧结果文件没有该键 → nil。
    public let sleeveParams: [String: [Double]]?
    /// 持仓纳入结论（summary 键 `held_assets`）：纳入项 + 跳过原因。
    /// 旧结果文件没有该键 → nil（界面整个区不显示）。
    public let heldAssets: HeldAssetsInfo?

    /// 显式 init：新增字段带默认值，保证既有调用点（单测构造结果）源码兼容。
    public init(generatedAt: String?, portfolio: PortfolioSummary, assets: [OptimizedAsset],
                benchmark: Benchmark?, testAssets: [TestAssetEstimate]?, warnings: [String]?,
                targetReturn: Double?, targetGap: Double?,
                warningItems: [OptimizationWarningItem]?, objective: String?,
                sourceDetail: String?,
                sleeveParams: [String: [Double]]? = nil,
                heldAssets: HeldAssetsInfo? = nil) {
        self.generatedAt = generatedAt
        self.portfolio = portfolio
        self.assets = assets
        self.benchmark = benchmark
        self.testAssets = testAssets
        self.warnings = warnings
        self.targetReturn = targetReturn
        self.targetGap = targetGap
        self.warningItems = warningItems
        self.objective = objective
        self.sourceDetail = sourceDetail
        self.sleeveParams = sleeveParams
        self.heldAssets = heldAssets
    }
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

public struct SensitivityPoint: Codable, Hashable, Sendable, Identifiable {
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
    /// 该点是否为「回退点」：目标在当前约束下不可行，交付收益低于该点目标（或阶段一目标被下调）。
    /// 这类点不是「目标设成它时能交付的组合」，不应画进蓝色前沿。旧输出没有该键 → nil。
    public let fallback: Bool?
    /// 候选包络的来源目标：该点统计借自更高目标的可行组合时才有值（加法式；旧输出 → nil）。
    public let sourceTarget: Double?
    /// 该点是否被「支配修复」用主结果袖参数重解并替换（加法式；旧输出 → nil）。
    public let repaired: Bool?
}

/// 敏感性锚点一致性检查里的一个落点（收益 / 波动 / 夏普）。
public struct AnchorMetrics: Codable, Hashable, Sendable {
    public let ret: Double?
    public let vol: Double?
    public let sharpe: Double?
}

/// 敏感性分析的锚点一致性块（Python 侧 `--anchor-*` 的加法式输出）。
///
/// 主结果的目标收益率被并进扫描网格后，这里给出「锚点目标点」与「主结果」的落点对比，
/// 用于说明曲线上锚点是否真的与结果页同源。字段全部 optional（加法式，旧输出没有该块）。
public struct AnchorInfo: Codable, Hashable, Sendable {
    public let target: Double?
    /// 锚点目标点与主结果是否一致（未被主结果支配）。
    public let matched: Bool?
    /// 锚点目标点的落点。
    public let point: AnchorMetrics?
    /// 主结果的落点。
    public let main: AnchorMetrics?
}

public struct SensitivityAnalysisResult: Codable, Sendable {
    public let maxFeasibleReturn: Double
    /// 扫描口径（Python 侧 scope 字段）：一句话说明这一点是怎么算出来的
    /// （2026-10-14 起 = 完整流水线：阶段一 + 袖内固定点 + 阶段二，与结果页同源）。
    public let scope: String?
    /// 肥尾模型自由度（尾部指数 α），对应 Python 端 --tail-dof
    public let tailDof: Double?
    public let rf: Double
    public let domesticWeight: Double
    public let overseasWeight: Double
    public let points: [SensitivityPoint]
    /// 锚点一致性检查块（带 `--anchor-ret/--anchor-vol` 调用时才有）。
    /// 旧输出 / 无锚点调用没有该键 → nil。
    public let anchor: AnchorInfo?
}
