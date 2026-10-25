import Foundation

// MARK: - 组合估值（唯一入口）
//
// 「组合此刻值多少」只在这里算一次：市值 / 折汇 / 权重 / 池 / 总成本 / asOfDate。
// Repository 只负责读库，视图 / 优化器 extract / pm-cli 只消费本文件产出的值。
// 口径见 docs/adr/0009-portfolio-valuation.md。

/// 最新价的来源口径。市值必须用**真实价格**：
/// 基金 `close` 是累计净值、权益 `close` 是复权价，都不是现实世界价格。
public enum PriceKind: String, Codable, Hashable, Sendable {
    /// 行情快照（基金 = 单位净值，权益 = 最新成交价）。
    case quote
    /// `prices.raw_close`：权益未复权收盘 / 基金单位净值。
    case realClose
    /// `prices.close`：复权收盘 / 累计净值。只有在没有真实现价时才用，且必须告警。
    case totalReturnClose
    /// 没有任何可用价格；市值按 0 计。
    case missing
}

/// 估值过程中的降级说明。**不阻塞**组合计算，只把「这个数是怎么来的」变成可见事实。
public enum ValuationWarning: Hashable, Sendable {
    case missingPrice(assetKey: String)
    case totalReturnBasis(assetKey: String)
    case missingFX(currency: String)
    case currencyMismatch(assetKey: String, holding: String, price: String)
    /// 持仓没有对应的 assets 行（数据不完整）；池按持仓币种回退。
    case missingAsset(assetKey: String)

    public var message: String {
        switch self {
        case .missingPrice(let key):
            return "\(key)：缺最新价，市值按 0 计"
        case .totalReturnBasis(let key):
            return "\(key)：缺真实现价，按总回报收盘估算"
        case .missingFX(let currency):
            return "\(currency)：缺汇率，按 1:1 估算"
        case .currencyMismatch(let key, let holding, let price):
            return "\(key)：行情币种 \(price) 与持仓币种 \(holding) 不一致"
        case .missingAsset(let key):
            return "\(key)：缺资产记录，池按持仓币种推定"
        }
    }
}

/// 单个持仓的估值结果（原币 + 折人民币）。
public struct PositionValuation: Hashable, Sendable {
    public let assetKey: String
    public let name: String
    /// 资产类别；无资产行时为 "其他"（与旧 fetchAllocation 一致）。
    public let assetClass: String
    /// 解析后的市场（闭集）；无资产行时为 nil。
    public let market: AssetMarket?
    /// 池归属（由市场派生；无资产行时按持仓币种回退，见 ADR 0010）。
    public let pool: Pool
    /// 持仓币种 —— 折算基准。
    public let currency: String
    public let quantity: Double
    public let costBasis: Double
    public let sortOrder: Double
    /// 是否有资产行。没有资产行的持仓计入总量、但不出现在资产明细（旧行为）。
    public let hasAsset: Bool

    /// 单价（原币）。nil = 没有任何可用价格；有价时 > 0。
    public let priceLocal: Double?
    public let priceDate: String?
    public let priceKind: PriceKind
    /// 汇率（1 外币 = ? CNY）。nil = 缺失/非法；折算时按 1.0。
    public let fxRate: Double?

    public let valueLocal: Double
    public let valueCny: Double
    public let costCny: Double
    public let weight: Double
    public let unrealizedPnlCny: Double
    public let returnRate: Double

    public var fxMissing: Bool { fxRate == nil }
}

/// 组合级估值快照（纯值，不碰数据库）。
public struct PortfolioValuation: Hashable, Sendable {
    /// 组合估值日 = 所有持仓里最晚的 `priceDate`（ISO yyyy-MM-dd 字典序）。
    public let asOfDate: String?
    public let positions: [PositionValuation]
    public let totalValueCny: Double
    public let totalCostCny: Double
    public let domesticValueCny: Double
    public let overseasValueCny: Double
    public let domesticCostCny: Double
    public let overseasCostCny: Double
    public let warnings: [ValuationWarning]

    /// 资产总览的投影。
    public var allocationSnapshot: AllocationSnapshot {
        struct Group {
            var value: Double
            var pool: Pool
            var dominant: Double
        }
        var groups: [String: Group] = [:]
        for p in positions {
            if var g = groups[p.assetClass] {
                g.value += p.valueCny
                // 混合大类的池：取组内市值绝对值最大的持仓，而不是“先插入者”。
                if abs(p.valueCny) > abs(g.dominant) {
                    g.dominant = p.valueCny
                    g.pool = p.pool
                }
                groups[p.assetClass] = g
            } else {
                groups[p.assetClass] = Group(value: p.valueCny, pool: p.pool, dominant: p.valueCny)
            }
        }
        let total = totalValueCny
        let slices = groups.map { (cls, g) in
            AllocationSlice(assetClass: cls, value: g.value,
                            weight: total > 0 ? g.value / total : 0, pool: g.pool)
        }.sorted { $0.value > $1.value }
        return AllocationSnapshot(
            asOfDate: asOfDate,
            totalValue: total,
            domesticValue: domesticValueCny,
            overseasValue: overseasValueCny,
            totalCost: totalCostCny,
            domesticCost: domesticCostCny,
            overseasCost: overseasCostCny,
            slices: slices)
    }

    /// 子集权重（优化器 extract 的 us_equity / held_assets 共用）。
    /// 只纳入 > 0 且有限的值；sum <= 0 → [:]。
    public static func weights(for values: [String: Double]) -> [String: Double] {
        var positive: [String: Double] = [:]
        var total = 0.0
        for (key, value) in values where value.isFinite && value > 0 {
            positive[key] = value
            total += value
        }
        guard total > 0 else { return [:] }
        return positive.mapValues { $0 / total }
    }

    /// 纯函数：一次装配出全部派生量。
    ///
    /// - Parameters:
    ///   - quotes: `quotes` 表最新价（基金 = 单位净值）。
    ///   - lastPrices: 仅对**缺有效 quote** 的持仓需要；由 Repository 用 LIMIT 1 查询提供。
    ///   - fxRates: 币种 → CNY；`CNY` 由调用方保证为 1.0。
    public static func make(holdings: [Holding],
                            assetsByKey: [String: Asset],
                            quotes: [String: Quote],
                            lastPrices: [String: PricePoint],
                            fxRates: [String: Double]) -> PortfolioValuation {
        struct Interim {
            let holding: Holding
            let asset: Asset?
            let priceLocal: Double?
            let priceDate: String?
            let priceKind: PriceKind
            let fxRate: Double?
            let pool: Pool
            let market: AssetMarket?
            let valueLocal: Double
            let valueCny: Double
            let costCny: Double
        }

        var interim: [Interim] = []
        var warnings: [ValuationWarning] = []
        var reportedFX = Set<String>()
        var reportedPrice = Set<String>()
        var reportedMissingAsset = Set<String>()

        for h in holdings {
            let asset = assetsByKey[h.assetKey]

            // 汇率：缺失 / <= 0 / 非有限 → 按 1:1，并每个币种只告警一次。
            let rawRate = fxRates[h.currency]
            let rateValid = rawRate.map { $0.isFinite && $0 > 0 } ?? false
            let rate = rateValid ? rawRate : nil
            if !rateValid, reportedFX.insert(h.currency).inserted {
                warnings.append(.missingFX(currency: h.currency))
            }

            // 价格：quote → rawClose → close（告警）→ missing。
            var price: Double?
            var priceDate: String?
            var priceKind: PriceKind = .missing
            var priceCurrency: String?
            if let q = quotes[h.assetKey], q.price.isFinite, q.price > 0 {
                price = q.price
                priceDate = q.date
                priceKind = .quote
                priceCurrency = q.currency
            } else if let bar = lastPrices[h.assetKey] {
                priceDate = bar.date
                priceCurrency = bar.currency
                if let raw = bar.rawClose, raw.isFinite, raw > 0 {
                    price = raw
                    priceKind = .realClose
                } else if bar.close.isFinite, bar.close > 0 {
                    price = bar.close
                    priceKind = .totalReturnClose
                }
            }
            if priceKind == .missing, reportedPrice.insert(h.assetKey).inserted {
                warnings.append(.missingPrice(assetKey: h.assetKey))
            } else if priceKind == .totalReturnClose, reportedPrice.insert(h.assetKey).inserted {
                warnings.append(.totalReturnBasis(assetKey: h.assetKey))
            }

            if let pc = priceCurrency, !pc.isEmpty,
               pc.caseInsensitiveCompare(h.currency) != .orderedSame {
                warnings.append(.currencyMismatch(assetKey: h.assetKey,
                                                holding: h.currency, price: pc))
            }

            // 身份（市场 / 池）一律经 AssetIdentity；无资产行按「key 当 ticker + 持仓币种」回退，
            // 并显式告警 —— 旧行为是硬编码境外（ADR 0010）。
            let identity: AssetIdentity
            if let asset {
                identity = AssetIdentity(asset)
            } else {
                identity = AssetIdentity(key: h.assetKey, ticker: h.assetKey,
                                         marketRaw: nil, currency: h.currency)
                if reportedMissingAsset.insert(h.assetKey).inserted {
                    warnings.append(.missingAsset(assetKey: h.assetKey))
                }
            }
            let pool = identity.pool
            let market = identity.market

            let valueLocal = h.quantity * (price ?? 0)
            let valueCny = valueLocal * (rate ?? 1)
            let costCny = h.costBasis * (rate ?? 1)
            interim.append(Interim(holding: h, asset: asset,
                                   priceLocal: price, priceDate: priceDate, priceKind: priceKind,
                                   fxRate: rate, pool: pool, market: market,
                                   valueLocal: valueLocal, valueCny: valueCny, costCny: costCny))
        }

        let totalValue = interim.reduce(0.0) { $0 + $1.valueCny }
        let totalCost = interim.reduce(0.0) { $0 + $1.costCny }
        var domesticValue = 0.0, overseasValue = 0.0
        var domesticCost = 0.0, overseasCost = 0.0
        for item in interim {
            switch item.pool {
            case .domestic:
                domesticValue += item.valueCny
                domesticCost += item.costCny
            case .overseas:
                overseasValue += item.valueCny
                overseasCost += item.costCny
            }
        }

        let positions = interim.map { item -> PositionValuation in
            let weight = totalValue > 0 ? item.valueCny / totalValue : 0
            let unrealized = item.valueCny - item.costCny
            return PositionValuation(
                assetKey: item.holding.assetKey,
                name: item.asset?.name ?? item.holding.assetKey,
                assetClass: item.asset?.assetClass ?? "其他",
                market: item.market,
                pool: item.pool,
                currency: item.holding.currency,
                quantity: item.holding.quantity,
                costBasis: item.holding.costBasis,
                sortOrder: item.asset?.sortOrder ?? 0,
                hasAsset: item.asset != nil,
                priceLocal: item.priceLocal,
                priceDate: item.priceDate,
                priceKind: item.priceKind,
                fxRate: item.fxRate,
                valueLocal: item.valueLocal,
                valueCny: item.valueCny,
                costCny: item.costCny,
                weight: weight,
                unrealizedPnlCny: unrealized,
                returnRate: item.costCny > 0 ? unrealized / item.costCny : 0)
        }

        return PortfolioValuation(
            asOfDate: positions.compactMap(\.priceDate).max(),
            positions: positions,
            totalValueCny: totalValue,
            totalCostCny: totalCost,
            domesticValueCny: domesticValue,
            overseasValueCny: overseasValue,
            domesticCostCny: domesticCost,
            overseasCostCny: overseasCost,
            warnings: warnings)
    }
}

// MARK: - 资产总览投影（从 Repository.swift 迁入）

/// One slice of the asset-allocation pie (grouped by asset class).
public struct AllocationSlice: Codable, Hashable, Identifiable {
    public let assetClass: String
    public let value: Double
    public let weight: Double
    public let pool: Pool
    public var id: String { assetClass }
}

/// Current portfolio allocation snapshot.
/// 境内/境外按每个标的的**市场**派生（`AssetMarket` → 池，见 Q26=C / Q31），
/// 与资产明细里显示的「池」一致；不再按资产大类分组取整组归属。
public struct AllocationSnapshot: Codable, Hashable {
    public let asOfDate: String?
    public let totalValue: Double
    public let domesticValue: Double
    public let overseasValue: Double
    /// 总成本 = Σ 各持仓「成本（折人民币）」；总市值 = totalValue。两者同口径（都含全部持仓）。
    public let totalCost: Double
    public let domesticCost: Double
    public let overseasCost: Double
    public let slices: [AllocationSlice]

    public init(asOfDate: String?, totalValue: Double, domesticValue: Double,
                overseasValue: Double, totalCost: Double = 0, domesticCost: Double = 0,
                overseasCost: Double = 0, slices: [AllocationSlice]) {
        self.asOfDate = asOfDate
        self.totalValue = totalValue
        self.domesticValue = domesticValue
        self.overseasValue = overseasValue
        self.totalCost = totalCost
        self.domesticCost = domesticCost
        self.overseasCost = overseasCost
        self.slices = slices
    }

    /// 境内/境外池占比（供优化器 / 基准线使用）。
    /// 池只有两种，二者按定义归一到 1；无持仓时回退 50/50（与旧的池权重口径一致）。
    public var poolShares: (domestic: Double, overseas: Double) {
        let used = domesticValue + overseasValue
        guard used > 0 else { return (0.5, 0.5) }
        return (domesticValue / used, overseasValue / used)
    }
}
