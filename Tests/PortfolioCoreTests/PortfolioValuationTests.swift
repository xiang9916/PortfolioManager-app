import XCTest
@testable import PortfolioCore

/// 组合估值 module 的口径测试（纯 fixture，不建库）。
///
/// 锁定 ADR 0009：
/// - 最新价 quote → rawClose → close（告警）→ missing；
/// - 缺汇率按 1:1 但必须告警；
/// - asOfDate = 最晚价格日；权重和 = 1；
/// - 池由市场派生；无资产行按持仓币种回退并告警（ADR 0010）。
final class PortfolioValuationTests: XCTestCase {

    // MARK: - fixtures

    private func asset(_ key: String,
                       ticker: String? = nil,
                       market: AssetMarket? = nil,
                       cls: String = "test_class",
                       currency: String = "CNY",
                       pool: Pool = .overseas,
                       sort: Double = 0) -> Asset {
        Asset(key: key, name: key, ticker: ticker, market: market,
              assetClass: cls, pool: pool, currency: currency, sortOrder: sort)
    }

    private func holding(_ key: String, qty: Double = 1, cost: Double = 1,
                         ccy: String = "CNY") -> Holding {
        Holding(assetKey: key, quantity: qty, costBasis: cost,
                currency: ccy, asOfDate: "2026-10-01")
    }

    private func quote(_ key: String, _ price: Double, ccy: String? = "CNY",
                       date: String = "2026-10-01") -> Quote {
        Quote(symbol: key, price: price, currency: ccy, date: date, source: "test")
    }

    private func bar(_ key: String, _ close: Double, raw: Double? = nil,
                     date: String = "2026-09-30", ccy: String = "CNY") -> PricePoint {
        PricePoint(assetKey: key, date: date, close: close, rawClose: raw, currency: ccy)
    }

    private func make(holdings: [Holding], assets: [Asset] = [],
                      quotes: [String: Quote] = [:],
                      bars: [String: PricePoint] = [:],
                      fx: [String: Double] = ["CNY": 1.0]) -> PortfolioValuation {
        PortfolioValuation.make(
            holdings: holdings,
            assetsByKey: Dictionary(uniqueKeysWithValues: assets.map { ($0.key, $0) }),
            quotes: quotes, lastPrices: bars, fxRates: fx)
    }

    // MARK: - 价格口径

    func testQuoteWinsAndPairsDate() {
        let v = make(holdings: [holding("F", qty: 10, cost: 5)],
                     assets: [asset("F", market: .cn, cls: "cn_fund")],
                     quotes: ["F": quote("F", 1.20, date: "2026-10-02")],
                     bars: ["F": bar("F", 5.60, raw: 1.10, date: "2026-09-30")])
        let p = v.positions[0]
        XCTAssertEqual(p.priceLocal, 1.20)
        XCTAssertEqual(p.priceDate, "2026-10-02")
        XCTAssertEqual(p.priceKind, .quote)
        XCTAssertEqual(p.valueCny, 12, accuracy: 1e-9)
    }

    func testFundWithoutQuoteUsesRealCloseNotCumulative() {
        // 基金：close = 累计净值 5.60，rawClose = 单位净值 5.54。市值必须用 5.54。
        let v = make(holdings: [holding("F", qty: 100, cost: 500)],
                     assets: [asset("F", market: .cn, cls: "cn_fund")],
                     bars: ["F": bar("F", 5.60, raw: 5.54)])
        let p = v.positions[0]
        XCTAssertEqual(p.priceLocal, 5.54)
        XCTAssertEqual(p.priceKind, .realClose)
        XCTAssertEqual(p.valueCny, 554, accuracy: 1e-9)
        XCTAssertTrue(v.warnings.isEmpty)
    }

    func testEquityWithoutQuoteUsesRawCloseNotAdjustedClose() {
        let v = make(holdings: [holding("S", qty: 2, cost: 100, ccy: "USD")],
                     assets: [asset("S", market: .us, cls: "us_equity", currency: "USD")],
                     bars: ["S": bar("S", 95, raw: 100, ccy: "USD")],
                     fx: ["CNY": 1.0, "USD": 7.0])
        let p = v.positions[0]
        XCTAssertEqual(p.priceLocal, 100)
        XCTAssertEqual(p.priceKind, .realClose)
        XCTAssertEqual(p.valueCny, 1400, accuracy: 1e-9)   // 2 × 100 × 7
        XCTAssertTrue(v.warnings.isEmpty, "币种一致、汇率齐全时不应有告警")
    }

    func testForeignPriceWithCNYHoldingKeepsCNYBasisAndWarns() {
        // 持仓按 CNY 记账、K 线是 USD：折算基准仍是**持仓币种**（1:1），但必须提示币种不一致。
        // 这条路径与 quote 版（testCurrencyMismatchWarnsButUsesHoldingCurrency）分开覆盖：
        // 价格来自 prices 末行时同样要能发出 currencyMismatch。
        let v = make(holdings: [holding("S", qty: 2, cost: 100)],
                     assets: [asset("S", market: .us, cls: "us_equity", currency: "USD")],
                     bars: ["S": bar("S", 95, raw: 100, ccy: "USD")],
                     fx: ["CNY": 1.0, "USD": 7.0])
        let p = v.positions[0]
        XCTAssertEqual(p.priceLocal, 100)
        XCTAssertEqual(p.valueCny, 200, accuracy: 1e-9)   // 持仓币种 CNY → 1:1
        XCTAssertTrue(v.warnings.contains(
            .currencyMismatch(assetKey: "S", holding: "CNY", price: "USD")))
    }

    func testZeroTotalFallsBackToFiftyFiftyPoolShares() {
        // 有持仓但没有任何可用价格：总市值 0 → 池占比回退 50/50（优化器 extract 的“无数据”兜底，
        // 与旧 livePoolWeights 一致）；持仓的池归属仍按市场派生，只是没有权重可算。
        let v = make(holdings: [holding("CN", qty: 100), holding("NOPRICE")],
                     assets: [asset("CN", market: .cn, cls: "cn_fund")])
        XCTAssertEqual(v.totalValueCny, 0)
        XCTAssertEqual(v.positions[0].pool, .domestic)
        XCTAssertEqual(v.positions[1].pool, .overseas)
        XCTAssertEqual(v.domesticValueCny, 0)
        XCTAssertEqual(v.overseasValueCny, 0)
        XCTAssertEqual(v.allocationSnapshot.poolShares.domestic, 0.5, accuracy: 1e-9)
        XCTAssertEqual(v.allocationSnapshot.poolShares.overseas, 0.5, accuracy: 1e-9)
    }

    func testMissingRawCloseFallsBackToTotalReturnWithWarning() {
        let v = make(holdings: [holding("F", qty: 10)],
                     assets: [asset("F", market: .cn, cls: "cn_fund")],
                     bars: ["F": bar("F", 5.60, raw: nil)])
        let p = v.positions[0]
        XCTAssertEqual(p.priceKind, .totalReturnClose)
        XCTAssertEqual(p.priceLocal, 5.60)
        XCTAssertTrue(v.warnings.contains(.totalReturnBasis(assetKey: "F")))
    }

    func testNoPriceIsMissingAndZeroValued() {
        let v = make(holdings: [holding("X", qty: 10, cost: 10)],
                     assets: [asset("X")])
        let p = v.positions[0]
        XCTAssertNil(p.priceLocal)
        XCTAssertNil(p.priceDate)
        XCTAssertEqual(p.priceKind, .missing)
        XCTAssertEqual(p.valueCny, 0)
        XCTAssertEqual(v.totalValueCny, 0)
        XCTAssertTrue(v.warnings.contains(.missingPrice(assetKey: "X")))
    }

    func testNonFiniteQuoteTreatedAsMissing() {
        let v = make(holdings: [holding("X", qty: 1)],
                     assets: [asset("X")],
                     quotes: ["X": quote("X", .nan)])
        XCTAssertEqual(v.positions[0].priceKind, .missing)
        XCTAssertTrue(v.totalValueCny.isFinite)
    }

    // MARK: - 汇率

    func testMissingFXUsesOneAndWarnsOncePerCurrency() {
        let v = make(holdings: [holding("A", qty: 1, ccy: "USD"), holding("B", qty: 1, ccy: "USD")],
                     assets: [asset("A", currency: "USD"), asset("B", currency: "USD")],
                     quotes: ["A": quote("A", 100, ccy: "USD"), "B": quote("B", 50, ccy: "USD")],
                     fx: ["CNY": 1.0])
        XCTAssertEqual(v.totalValueCny, 150, accuracy: 1e-9)   // 1:1
        XCTAssertEqual(v.positions.filter { $0.fxMissing }.count, 2)
        XCTAssertEqual(v.warnings.filter { $0 == .missingFX(currency: "USD") }.count, 1)
    }

    func testInvalidFXRateTreatedAsMissing() {
        let v = make(holdings: [holding("A", qty: 1, ccy: "USD")],
                     assets: [asset("A", currency: "USD")],
                     quotes: ["A": quote("A", 100, ccy: "USD")],
                     fx: ["CNY": 1.0, "USD": 0])
        XCTAssertEqual(v.totalValueCny, 100, accuracy: 1e-9)
        XCTAssertTrue(v.warnings.contains(.missingFX(currency: "USD")))
    }

    func testCurrencyMismatchWarnsButUsesHoldingCurrency() {
        // 持仓按 CNY 记账、行情是 USD：仍按持仓币种折算（1:1），但必须提示。
        let v = make(holdings: [holding("S", qty: 1, ccy: "CNY")],
                     assets: [asset("S", market: .us, currency: "USD")],
                     quotes: ["S": quote("S", 100, ccy: "USD")],
                     fx: ["CNY": 1.0, "USD": 7.0])
        XCTAssertEqual(v.totalValueCny, 100, accuracy: 1e-9)
        XCTAssertTrue(v.warnings.contains(
            .currencyMismatch(assetKey: "S", holding: "CNY", price: "USD")))
    }

    // MARK: - 时间 / 权重 / 池

    func testAsOfDateIsLatestPriceDate() {
        let v = make(holdings: [holding("A"), holding("B")],
                     assets: [asset("A"), asset("B")],
                     quotes: ["A": quote("A", 1, date: "2026-09-01"),
                              "B": quote("B", 1, date: "2026-10-01")])
        XCTAssertEqual(v.asOfDate, "2026-10-01")
    }

    func testWeightsSumToOneAndZeroTotal() {
        let v = make(holdings: [holding("A", qty: 100), holding("B", qty: 300)],
                     assets: [asset("A"), asset("B")],
                     quotes: ["A": quote("A", 1), "B": quote("B", 1)])
        XCTAssertEqual(v.totalValueCny, 400, accuracy: 1e-9)
        XCTAssertEqual(v.positions[0].weight, 0.25, accuracy: 1e-9)
        XCTAssertEqual(v.positions[1].weight, 0.75, accuracy: 1e-9)
        XCTAssertEqual(v.positions.map(\.weight).reduce(0, +), 1, accuracy: 1e-9)

        let empty = make(holdings: [])
        XCTAssertEqual(empty.totalValueCny, 0)
        XCTAssertNil(empty.asOfDate)
        XCTAssertEqual(empty.allocationSnapshot.poolShares.domestic, 0.5, accuracy: 1e-9)
    }

    func testAssetlessHoldingUsesCurrencyFallbackAndWarns() {
        let v = make(holdings: [holding("CN", qty: 100), holding("FICT-1")],
                     assets: [asset("CN", market: .cn, cls: "cn_fund")],
                     quotes: ["CN": quote("CN", 1)])
        XCTAssertEqual(v.positions[0].pool, .domestic)
        XCTAssertEqual(v.positions[1].pool, .domestic)   // 无资产行 → 按持仓币种（CNY）回退
        XCTAssertTrue(v.warnings.contains(.missingAsset(assetKey: "FICT-1")))
        XCTAssertEqual(v.allocationSnapshot.poolShares.domestic, 1.0, accuracy: 1e-9)
    }

    func testAssetlessForeignHoldingStaysOverseasAndWarns() {
        let v = make(holdings: [holding("FICT", qty: 1, ccy: "USD")], assets: [], quotes: [:])
        XCTAssertEqual(v.positions[0].pool, .overseas)
        XCTAssertTrue(v.warnings.contains(.missingAsset(assetKey: "FICT")))
    }

    func testAllocationSlicesUseDominantPool() {
        // 同一类别里境内 100 / 境外 300：slice 的池取组内最大市值者。
        let v = make(holdings: [holding("CN", qty: 100), holding("US", qty: 300)],
                     assets: [asset("CN", market: .cn, cls: "mixed"),
                              asset("US", market: .us, cls: "mixed", currency: "USD")],
                     quotes: ["CN": quote("CN", 1), "US": quote("US", 1, ccy: "USD")],
                     fx: ["CNY": 1.0, "USD": 1.0])
        let slice = v.allocationSnapshot.slices[0]
        XCTAssertEqual(slice.assetClass, "mixed")
        XCTAssertEqual(slice.value, 400, accuracy: 1e-9)
        XCTAssertEqual(slice.weight, 1, accuracy: 1e-9)
        XCTAssertEqual(slice.pool, .overseas)
    }

    func testSubsetWeightsNormalizeWithinSubset() {
        let w = PortfolioValuation.weights(for: ["a": 100, "b": 300, "c": 0, "d": -5])
        XCTAssertEqual(w["a"], 0.25)
        XCTAssertEqual(w["b"], 0.75)
        XCTAssertNil(w["c"])
        XCTAssertNil(w["d"])
        XCTAssertTrue(PortfolioValuation.weights(for: ["a": 0]).isEmpty)
    }
}
