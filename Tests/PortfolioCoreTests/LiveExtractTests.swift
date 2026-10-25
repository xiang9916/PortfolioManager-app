import XCTest
@testable import PortfolioCore

/// `LiveExtract`（App ↔ Python 的输入契约）的行为测试 + 与改造前实现的等价门。
///
/// fixture 全部虚构：标的代码只用 6 位占位数字与字母代号，金额是随手写的整数。
final class LiveExtractTests: XCTestCase {

    // MARK: - 基础行为

    func testPoolStatsWrittenOnlyWhenPortfolioHasValue() {
        let rows = [live("A", assetClass: "us_equity", market: .us, pool: .overseas, valueCny: 100)]
        let base: [String: Any] = ["pool_mode": "frozen", "domestic_value": 1.0]

        let untouched = LiveExtract.apply(to: base, LiveExtract.Input(
            allocation: alloc(total: 0, dom: 0, ovs: 0), rows: rows, cn10y: 0.02, us10y: 0.04))
        XCTAssertNil(untouched["domestic_weight"])
        XCTAssertEqual(untouched["pool_mode"] as? String, "frozen")
        XCTAssertEqual(untouched["domestic_value"] as? Double, 1.0)

        let applied = LiveExtract.apply(to: base, LiveExtract.Input(
            allocation: alloc(total: 400, dom: 100, ovs: 300), rows: rows, cn10y: 0.02, us10y: 0.04))
        XCTAssertEqual(applied["pool_mode"] as? String, "app_live")
        XCTAssertEqual(applied["total_value"] as? Double, 400)
        XCTAssertEqual(applied["domestic_weight"] as? Double, 0.25)
        XCTAssertEqual(applied["overseas_weight"] as? Double, 0.75)
        XCTAssertEqual(applied["rf"] as? Double, 0.75 * 0.04 + 0.25 * 0.02)
    }

    func testUsEquityOmittedWhenEmpty() {
        let base: [String: Any] = ["us_equity": ["total_value": 9.0, "holdings": []]]
        let out = LiveExtract.apply(to: base, LiveExtract.Input(
            allocation: nil,
            rows: [live("B", assetClass: "broad", market: .cn, pool: .domestic, valueCny: 50)],
            cn10y: nil, us10y: nil))
        let usEquity = out["us_equity"] as? [String: Any]
        XCTAssertEqual(usEquity?["total_value"] as? Double, 9.0, "美股为空时不得覆盖提取文件里的冻结值")
        XCTAssertEqual((usEquity?["holdings"] as? [Any])?.count, 0)
    }

    func testUsEquityWeightsUseSubsetDenominator() {
        let rows = [live("A", assetClass: "us_equity", market: .us, pool: .overseas, valueCny: 300),
                    live("C", assetClass: "us_equity", market: .us, pool: .overseas, valueCny: 100),
                    live("D", assetClass: "broad", market: .cn, pool: .domestic, valueCny: 400)]
        let out = LiveExtract.apply(to: [:], LiveExtract.Input(allocation: nil, rows: rows,
                                                               cn10y: nil, us10y: nil))
        let usEquity = out["us_equity"] as? [String: Any]
        XCTAssertEqual(usEquity?["total_value"] as? Double, 400)
        let holdings = (usEquity?["holdings"] as? [[String: Any]]) ?? []
        XCTAssertEqual(holdings.map { $0["ticker"] as? String }, ["A", "C"])
        XCTAssertEqual(holdings[0]["weight"] as? Double, 0.75)
        XCTAssertEqual(holdings[1]["weight"] as? Double, 0.25)
    }

    func testDomesticFundsOnlySixDigitCodes() {
        let rows = [live("F1", ticker: "123456.CN_FUND", assetClass: "fund", market: .cn,
                         pool: .domestic, valueCny: 10),
                    live("F2", assetClass: "fund", market: .cn, pool: .domestic, valueCny: 0),
                    live("F3", ticker: "510300.SS", assetClass: "broad", market: .cn,
                         pool: .domestic, valueCny: 20),
                    live("123456", assetClass: "fund", market: .cn, pool: .domestic, valueCny: 30)]
        let out = LiveExtract.apply(to: [:], LiveExtract.Input(allocation: nil, rows: rows,
                                                               cn10y: nil, us10y: nil))
        let funds = (out["domestic_holdings"] as? [[String: Any]]) ?? []
        XCTAssertEqual(funds.compactMap { $0["code"] as? String }, ["123456", "123456"])
        XCTAssertEqual(funds.count, 2, "境内基金只收 6 位代码；.SS 是 A 股后缀、F2 不是代码；零市值也照收")
    }

    func testTickerFallsBackToAssetKey() {
        let rows = [live("FICT-A", assetClass: "us_equity", market: .us, pool: .overseas,
                         valueCny: 10)]
        let out = LiveExtract.apply(to: [:], LiveExtract.Input(allocation: nil, rows: rows,
                                                               cn10y: nil, us10y: nil))
        let held = (out["held_assets"] as? [[String: Any]]) ?? []
        XCTAssertEqual(held.first?["ticker"] as? String, "FICT-A")
        let usHoldings = (out["us_equity"] as? [String: Any])?["holdings"] as? [[String: Any]]
        XCTAssertEqual(usHoldings?.first?["ticker"] as? String, "FICT-A")
    }

    func testHeldAssetsAlwaysWrittenAndSwitchesOn() {
        let out = LiveExtract.apply(to: [:], LiveExtract.Input(allocation: nil, rows: [],
                                                               cn10y: nil, us10y: nil))
        XCTAssertEqual((out["held_assets"] as? [Any])?.count, 0)
        XCTAssertEqual(out["include_unmodeled_holdings"] as? Bool, true)
        XCTAssertEqual(out["emit_sensitivity_anchor"] as? Bool, true)
    }

    func testMarketPrefersStoredThenRowThenEmpty() {
        let rows = [live("A", assetClass: "broad", market: .cn, pool: .overseas, valueCny: 30,
                         assetMarket: .us),
                    live("B", assetClass: "broad", market: .cn, pool: .domestic, valueCny: 20),
                    live("C", assetClass: "broad", market: nil, pool: .overseas, valueCny: 10)]
        let out = LiveExtract.apply(to: [:], LiveExtract.Input(allocation: nil, rows: rows,
                                                               cn10y: nil, us10y: nil))
        let held = (out["held_assets"] as? [[String: Any]]) ?? []
        XCTAssertEqual(held.map { $0["market"] as? String }, ["US", "CN", ""],
                       "market 写的是 AssetMarket.rawValue（大写）")
    }

    func testAssetClassPrefersStoredAndZeroValueRowsDropped() {
        let rows = [live("A", assetClass: "fund", market: .cn, pool: .domestic,
                         valueCny: 10, storedClass: "us_equity"),
                    live("B", assetClass: "bond", market: .cn, pool: .domestic,
                         valueCny: 0, storedClass: nil)]
        let out = LiveExtract.apply(to: [:], LiveExtract.Input(allocation: nil, rows: rows,
                                                               cn10y: nil, us10y: nil))
        let held = (out["held_assets"] as? [[String: Any]]) ?? []
        XCTAssertEqual(held.count, 1, "市值 0 的行不进 held_assets")
        XCTAssertEqual(held.first?["asset_class"] as? String, "us_equity",
                       "asset_class 优先取 assets 表")
        XCTAssertNil(out["us_equity"], "美股袖按资产明细行的 asset_class 判定，不受 assets 表影响")
    }

    func testRfRequiresBothRates() {
        let rows = [live("A", assetClass: "broad", market: .us, pool: .overseas, valueCny: 100)]
        let onlyCN = LiveExtract.apply(to: [:], LiveExtract.Input(
            allocation: alloc(total: 100, dom: 0, ovs: 100), rows: rows, cn10y: 0.02, us10y: nil))
        XCTAssertNil(onlyCN["rf"])

        let emptyPortfolio = LiveExtract.apply(to: [:], LiveExtract.Input(
            allocation: alloc(total: 0, dom: 0, ovs: 0), rows: rows, cn10y: 0.02, us10y: 0.04))
        XCTAssertNil(emptyPortfolio["rf"], "没有组合市值时池权重不参与计算，不写 rf")
    }

    // MARK: - 等价门：与改造前的字典改写逐键一致

    func testMatchesLegacyDictionaryRewrite() throws {
        let rows: [(AssetPerspectiveRow, Asset?)] = [
            (perspective("AAA", ticker: "AAA", assetClass: "us_equity", market: .us,
                         pool: .overseas, valueCny: 300),
             asset("AAA", ticker: "AAA", market: .us, assetClass: "us_equity")),
            (perspective("BBB", ticker: "BBB", assetClass: "us_equity", market: nil,
                         pool: .overseas, valueCny: 100),
             asset("BBB", ticker: "BBB", market: nil, assetClass: nil)),
            (perspective("123456", ticker: "123456.CN_FUND", assetClass: "fund", market: .cn,
                         pool: .domestic, valueCny: 40),
             asset("123456", ticker: "123456.CN_FUND", market: .cn, assetClass: "fund")),
            (perspective("FICT-1", ticker: nil, assetClass: "fund", market: .cn,
                         pool: .domestic, valueCny: 0), nil),
            (perspective("ETF", ticker: "510300.SS", assetClass: "broad", market: .cn,
                         pool: .domestic, valueCny: 60),
             asset("ETF", ticker: "510300.SS", market: .cn, assetClass: "broad")),
            (perspective("NOMKT", ticker: "NOMKT", assetClass: "broad", market: nil,
                         pool: .overseas, valueCny: 20),
             asset("NOMKT", ticker: "NOMKT", market: nil, assetClass: "broad")),
        ]
        let perspectives = rows.map { $0.0 }
        let assetsByKey = Dictionary(uniqueKeysWithValues: rows.compactMap { pair in
            pair.1.map { ($0.key, $0) }
        })
        let alloc = AllocationSnapshot(asOfDate: "2026-01-31", totalValue: 520,
                                       domesticValue: 100, overseasValue: 420,
                                       totalCost: 0, domesticCost: 0, overseasCost: 0, slices: [])
        let base: [String: Any] = ["generated_at": "2026-01-31",
                                   "us_equity": ["total_value": 0.0] as [String: Any]]

        let legacy = legacyRewrite(base: base, allocation: alloc, perspectives: perspectives,
                                   assetsByKey: assetsByKey, cn10y: 0.018, us10y: 0.042)
        let mine = LiveExtract.apply(to: base, LiveExtract.Input(
            allocation: LiveExtract.Allocation(
                totalValue: alloc.totalValue, domesticValue: alloc.domesticValue,
                overseasValue: alloc.overseasValue,
                domesticWeight: alloc.poolShares.domestic,
                overseasWeight: alloc.poolShares.overseas),
            rows: perspectives.map { p in
                let a = assetsByKey[p.assetKey]
                return LiveExtract.Row(assetKey: p.assetKey, ticker: a?.ticker, name: p.name,
                                       assetMarket: a?.market?.rawValue,
                                       rowMarket: p.market?.rawValue,
                                       assetClass: p.assetClass,
                                       storedAssetClass: a?.assetClass,
                                       pool: p.pool.rawValue,
                                       valueCny: p.valueCny)
            },
            cn10y: 0.018, us10y: 0.042))

        XCTAssertEqual(try canonical(mine), try canonical(legacy),
                       "新实现必须与改造前的字典改写逐键相等")
    }

    // MARK: - 改造前实现（oracle）

    /// 改造前 `AppStore.liveExtractURL()` 的逐字移植（去掉文件 I/O 与 db 读取）。
    /// 保留它当 oracle：等价门不依赖任何外部快照文件，可长期复跑。
    private func legacyRewrite(base: [String: Any],
                               allocation: AllocationSnapshot?,
                               perspectives: [AssetPerspectiveRow],
                               assetsByKey: [String: Asset],
                               cn10y: Double?, us10y: Double?) -> [String: Any] {
        var obj = base
        var domW = 0.0, ovW = 0.0
        if let alloc = allocation, alloc.totalValue > 0 {
            obj["pool_mode"] = "app_live"
            obj["domestic_value"] = alloc.domesticValue
            obj["overseas_value"] = alloc.overseasValue
            obj["total_value"] = alloc.totalValue
            let shares = alloc.poolShares
            domW = shares.domestic
            ovW = shares.overseas
            obj["domestic_weight"] = domW
            obj["overseas_weight"] = ovW
        }

        let usRows = perspectives.filter { $0.assetClass == "us_equity" && $0.valueCny > 0 }
        if !usRows.isEmpty {
            let totalUs = usRows.reduce(0.0) { $0 + $1.valueCny }
            let usWeights = PortfolioValuation.weights(for: Dictionary(
                uniqueKeysWithValues: usRows.map { ($0.assetKey, $0.valueCny) }))
            let holdings = usRows.sorted { $0.valueCny > $1.valueCny }.map { row -> [String: Any] in
                let ticker = (assetsByKey[row.assetKey]?.ticker) ?? row.assetKey
                return ["ticker": ticker, "name": row.name, "value_cny": row.valueCny,
                        "weight": usWeights[row.assetKey] ?? 0]
            }
            obj["us_equity"] = ["total_value": totalUs, "holdings": holdings] as [String: Any]
        }

        let domFunds: [[String: Any]] = perspectives.filter { $0.pool == .domestic }
            .compactMap { row -> [String: Any]? in
                let a = assetsByKey[row.assetKey]
                let raw = a?.ticker ?? row.assetKey
                guard let code = AssetTicker.fundCode(raw) else { return nil }
                return ["code": code, "name": row.name, "value_cny": row.valueCny]
            }
        if !domFunds.isEmpty { obj["domestic_holdings"] = domFunds }

        let heldRows = perspectives.filter { $0.valueCny > 0 }
        let heldWeights = PortfolioValuation.weights(for: Dictionary(
            uniqueKeysWithValues: heldRows.map { ($0.assetKey, $0.valueCny) }))
        let heldAssets: [[String: Any]] = heldRows.sorted { $0.valueCny > $1.valueCny }.map { row in
            let a = assetsByKey[row.assetKey]
            return ["ticker": a?.ticker ?? row.assetKey,
                    "name": row.name,
                    "market": a?.market?.rawValue ?? row.market?.rawValue ?? "",
                    "asset_class": a?.assetClass ?? row.assetClass,
                    "pool": row.pool.rawValue,
                    "value_cny": row.valueCny,
                    "weight": heldWeights[row.assetKey] ?? 0]
        }
        obj["held_assets"] = heldAssets
        obj["include_unmodeled_holdings"] = true
        obj["emit_sensitivity_anchor"] = true

        if let cn = cn10y, let us = us10y, domW > 0 || ovW > 0 {
            obj["rf"] = ovW * us + domW * cn
        }
        return obj
    }

    // MARK: - helpers

    private func alloc(total: Double, dom: Double, ovs: Double) -> LiveExtract.Allocation {
        let used = dom + ovs
        return LiveExtract.Allocation(totalValue: total, domesticValue: dom, overseasValue: ovs,
                                      domesticWeight: used > 0 ? dom / used : 0.5,
                                      overseasWeight: used > 0 ? ovs / used : 0.5)
    }

    private func live(_ key: String, ticker: String? = nil, assetClass: String,
                      market: AssetMarket?, pool: Pool, valueCny: Double,
                      assetMarket: AssetMarket? = nil, storedClass: String? = nil) -> LiveExtract.Row {
        LiveExtract.Row(assetKey: key, ticker: ticker, name: "name-" + key,
                        assetMarket: assetMarket?.rawValue, rowMarket: market?.rawValue,
                        assetClass: assetClass, storedAssetClass: storedClass,
                        pool: pool.rawValue, valueCny: valueCny)
    }

    private func asset(_ key: String, ticker: String?, market: AssetMarket?,
                       assetClass: String?) -> Asset {
        Asset(key: key, name: "name-" + key, ticker: ticker, market: market,
              assetClass: assetClass, pool: .overseas, currency: "USD")
    }

    private func perspective(_ key: String, ticker: String?, assetClass: String,
                             market: AssetMarket?, pool: Pool, valueCny: Double) -> AssetPerspectiveRow {
        AssetPerspectiveRow(assetKey: key, name: "name-" + key, assetClass: assetClass,
                            market: market, pool: pool, currency: "USD",
                            value: valueCny, valueCny: valueCny, costCny: 0,
                            unrealizedPnl: 0, returnRate: 0, quantity: 1, costBasis: 0,
                            weight: 0, latestPrice: nil, latestDate: nil, sortOrder: 0,
                            dividend: nil)
    }

    private func canonical(_ obj: [String: Any]) throws -> Data {
        try JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys])
    }
}
