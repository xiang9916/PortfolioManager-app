import Foundation

/// 把「App 的实时统计」写进优化器提取文件（`extract_app.json` → `extract_live.json`）。
///
/// 这是 App 与 Python 之间**唯一**的输入契约面：池权重、美股持仓、境内基金持仓、
/// 全部持仓（`held_assets`）、两个开关与 `rf` 都在这里决定。
///
/// 抽成纯函数的理由：它过去是 `AppStore` 里 88 行字典改写，而 App 层没有 testTarget
/// —— 「Python 到底看到什么」在 Swift 侧完全没有测试（ADR 0011）。
///
/// 行为与原实现逐键一致（门控条件、键名、取值来源）；改动任何一条都要同步更新
/// `LiveExtractTests` 里的期望 JSON 与 `Optimizer/tests/test_held_assets.py`。
public enum LiveExtract {

    /// 境内 / 境外池的实时统计（已折人民币）。
    public struct Allocation: Sendable {
        public let totalValue: Double
        public let domesticValue: Double
        public let overseasValue: Double
        public let domesticWeight: Double
        public let overseasWeight: Double

        public init(totalValue: Double, domesticValue: Double, overseasValue: Double,
                    domesticWeight: Double, overseasWeight: Double) {
            self.totalValue = totalValue
            self.domesticValue = domesticValue
            self.overseasValue = overseasValue
            self.domesticWeight = domesticWeight
            self.overseasWeight = overseasWeight
        }
    }

    /// 一行持仓（资产明细里的一个标的）。
    ///
    /// 同一个字段有「assets 表」与「资产明细行」两个来源时都要带上：
    /// 原实现对 ticker/market/asset_class 的取值是「assets 优先、明细行兜底」，
    /// 这里保留同样的优先级，否则没有 assets 行的持仓会写出不同的值。
    public struct Row: Sendable {
        public let assetKey: String
        /// `assets.ticker`；没有 assets 行或 ticker 为空时为 nil。
        public let ticker: String?
        public let name: String
        /// `assets.market` 的原始值。
        public let assetMarket: String?
        /// 资产明细行上的市场（兜底）。
        public let rowMarket: String?
        /// 资产明细行的 `asset_class`（美股袖的筛选口径用它）。
        public let assetClass: String
        /// `assets.asset_class`（`held_assets` 的取值优先用它）。
        public let storedAssetClass: String?
        /// 资产明细行的池（读路径派生值）。
        public let pool: String
        public let valueCny: Double

        public init(assetKey: String, ticker: String?, name: String,
                    assetMarket: String?, rowMarket: String?,
                    assetClass: String, storedAssetClass: String?,
                    pool: String, valueCny: Double) {
            self.assetKey = assetKey
            self.ticker = ticker
            self.name = name
            self.assetMarket = assetMarket
            self.rowMarket = rowMarket
            self.assetClass = assetClass
            self.storedAssetClass = storedAssetClass
            self.pool = pool
            self.valueCny = valueCny
        }
    }

    public struct Input: Sendable {
        public let allocation: Allocation?
        public let rows: [Row]
        public let cn10y: Double?
        public let us10y: Double?

        public init(allocation: Allocation?, rows: [Row], cn10y: Double?, us10y: Double?) {
            self.allocation = allocation
            self.rows = rows
            self.cn10y = cn10y
            self.us10y = us10y
        }
    }

    /// 在提取文件的字典上叠加 live 统计，返回新字典（不修改入参）。
    public static func apply(to base: [String: Any], _ input: Input) -> [String: Any] {
        var obj = base

        // 池统计：只有拿到非空组合时才覆盖（否则保留提取时的冻结值）。
        var domW = 0.0, ovW = 0.0
        if let alloc = input.allocation, alloc.totalValue > 0 {
            obj["pool_mode"] = "app_live"
            obj["domestic_value"] = alloc.domesticValue
            obj["overseas_value"] = alloc.overseasValue
            obj["total_value"] = alloc.totalValue
            domW = alloc.domesticWeight
            ovW = alloc.overseasWeight
            obj["domestic_weight"] = domW
            obj["overseas_weight"] = ovW
        }

        // us_equity：实时美股持仓（替代导入时的冻结快照）。
        // 美股为空时**不覆盖** —— Python 的 us_core_params 对空权重会 div-zero。
        let usRows = input.rows.filter { $0.assetClass == "us_equity" && $0.valueCny > 0 }
        if !usRows.isEmpty {
            let totalUs = usRows.reduce(0.0) { $0 + $1.valueCny }
            let weights = PortfolioValuation.weights(for: Dictionary(
                uniqueKeysWithValues: usRows.map { ($0.assetKey, $0.valueCny) }))
            let holdings = usRows.sorted { $0.valueCny > $1.valueCny }.map { row -> [String: Any] in
                ["ticker": row.ticker ?? row.assetKey,
                 "name": row.name,
                 "value_cny": row.valueCny,
                 "weight": weights[row.assetKey] ?? 0]
            }
            obj["us_equity"] = ["total_value": totalUs, "holdings": holdings] as [String: Any]
        }

        // domestic_holdings：实时境内基金持仓（动态锚定，替代 params.py 写死的 fund_code）。
        // 只收 6 位基金代码；其它境内标的（ETF/股票）不进这一节。
        let domesticFunds: [[String: Any]] = input.rows.filter { $0.pool == "domestic" }
            .compactMap { row -> [String: Any]? in
                let raw = row.ticker ?? row.assetKey
                guard let code = AssetTicker.fundCode(raw) else { return nil }
                return ["code": code, "name": row.name, "value_cny": row.valueCny]
            }
        if !domesticFunds.isEmpty { obj["domestic_holdings"] = domesticFunds }

        // held_assets：**全部** value>0 的持仓（不只美股/境内基金）。
        // Python 侧据此判断哪些持仓不在候选目录内、需要按 what-if 口径注入候选池。
        let heldRows = input.rows.filter { $0.valueCny > 0 }
        let heldWeights = PortfolioValuation.weights(for: Dictionary(
            uniqueKeysWithValues: heldRows.map { ($0.assetKey, $0.valueCny) }))
        let heldAssets: [[String: Any]] = heldRows.sorted { $0.valueCny > $1.valueCny }.map { row in
            ["ticker": row.ticker ?? row.assetKey,
             "name": row.name,
             "market": row.assetMarket ?? row.rowMarket ?? "",
             "asset_class": row.storedAssetClass ?? row.assetClass,
             "pool": row.pool,
             "value_cny": row.valueCny,
             "weight": heldWeights[row.assetKey] ?? 0]
        }
        // 无条件写（可以是空数组）：Python 侧据此知道 App 走的是新路径。
        obj["held_assets"] = heldAssets
        // 允许 Python 侧把目录外持仓注入为 what-if 资产（缺键 = 旧行为）。
        obj["include_unmodeled_holdings"] = true
        // Q2 锚定所需的 sleeve_params 由 Python 侧按此开关决定是否写进 detail/summary。
        obj["emit_sensitivity_anchor"] = true

        // RF：池比例加权中美 10 年期国债收益率（params.py 里不写死利率，取不到就按 0 计）。
        if let cn = input.cn10y, let us = input.us10y, domW > 0 || ovW > 0 {
            obj["rf"] = ovW * us + domW * cn
        }
        return obj
    }
}
