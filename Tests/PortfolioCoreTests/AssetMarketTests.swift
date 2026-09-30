import XCTest
@testable import PortfolioCore

/// 市场（闭集）→ 池 / 税率的派生口径测试。
///
/// 锁定这些决策：市场只有 US/HK/CN/JP/SG 五种，不再接受自由文本（Q32）；
/// 市场按 ticker 推断（`.HK`/`.T`/`.SI`/`.CN_FUND`/6 位数字/纯字母，Q26=C）；
/// 池由市场派生、读时计算，`assets.pool` 列只是缓存（Q20=C / Q27 / Q31）；
/// 池与股息税率必须从同一个市场结论出发，不允许各自判定。
final class AssetMarketTests: XCTestCase {
    private var tmpDir: URL!

    override func setUpWithError() throws {
        tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("pm-market-tests-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: tmpDir, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: tmpDir)
    }

    private func makeDB(_ name: String = "market.db") throws -> Database {
        try Database(path: tmpDir.appendingPathComponent(name).path)
    }

    // MARK: - ticker 推断（覆盖各条推断规则）

    func testTickerInferenceRules() {
        let cases: [(String, AssetMarket?)] = [
            ("000001", .cn), ("159915", .cn), ("511010", .cn),
            ("001234.CN_Fund", .cn), ("002345.CN_Fund", .cn), ("003456.CN_FUND", .cn),
            ("1111.HK", .hk), ("2222.HK", .hk), ("3333.HK", .hk),
            ("4444.T", .jp), ("5555.SI", .sg), ("AAA-USD", .us),
            ("BBB", .us), ("CCC", .us), ("DDD", .us), ("EEE", .us), ("FFF", .us),
            // 规则表外的情况
            ("159307.SZ", .cn), ("600519.SS", .cn), ("600519.SH", .cn),
            ("vod.l", nil), ("1234", nil), ("", nil),
        ]
        for (ticker, want) in cases {
            XCTAssertEqual(AssetMarket.infer(ticker: ticker), want, "ticker=\(ticker)")
        }
        XCTAssertNil(AssetMarket.infer(ticker: nil))
    }

    func testPoolDerivationSplitsSixDomesticElevenOverseas() {
        let portfolio = ["000001", "159915", "511010", "001234.CN_Fund", "002345.CN_Fund",
                         "003456.CN_FUND", "1111.HK", "2222.HK", "3333.HK", "4444.T",
                         "5555.SI", "AAA-USD", "BBB", "CCC", "DDD", "EEE", "FFF"]
        let pools = portfolio.map { AssetMarket.pool(market: nil, ticker: $0, currency: "CNY") }
        XCTAssertEqual(pools.filter { $0 == .domestic }.count, 6)
        XCTAssertEqual(pools.filter { $0 == .overseas }.count, 11)
    }

    // MARK: - 归一化：闭集，别无其它（Q32）

    func testCanonicalAliasesAndClosedSet() {
        XCTAssertEqual(AssetMarket.canonical(from: "nasdaq"), .us)
        XCTAssertEqual(AssetMarket.canonical(from: "U.S."), .us)
        XCTAssertEqual(AssetMarket.canonical(from: "NYSE ARCA"), .us)   // 复合写法
        XCTAssertEqual(AssetMarket.canonical(from: "hkex"), .hk)
        XCTAssertEqual(AssetMarket.canonical(from: "港股"), .hk)
        XCTAssertEqual(AssetMarket.canonical(from: "SH"), .cn)
        XCTAssertEqual(AssetMarket.canonical(from: "SSE"), .cn)
        XCTAssertEqual(AssetMarket.canonical(from: "中国"), .cn)
        XCTAssertEqual(AssetMarket.canonical(from: "jpx"), .jp)
        XCTAssertEqual(AssetMarket.canonical(from: "sgx"), .sg)
        // 不在闭集内 → nil（不保留原文）
        XCTAssertNil(AssetMarket.canonical(from: "LSE"))
        XCTAssertNil(AssetMarket.canonical(from: "XETRA"))
        XCTAssertNil(AssetMarket.canonical(from: "  "))
        XCTAssertNil(AssetMarket.canonical(from: nil))
        XCTAssertEqual(AssetMarket.allCases.map(\.rawValue), ["US", "HK", "CN", "JP", "SG"])
    }

    func testMarketWinsOverTicker() {
        XCTAssertEqual(AssetMarket.resolve(market: "JP", ticker: "GOOG"), .jp)
        XCTAssertEqual(AssetMarket.resolve(market: nil, ticker: "GOOG"), .us)
        XCTAssertEqual(AssetMarket.resolve(market: "LSE", ticker: "GOOG"), .us,
                       "不认识的原文不算数，回退到 ticker 推断")
        XCTAssertNil(AssetMarket.resolve(market: nil, ticker: "VOD.L"))
    }

    // MARK: - 池

    func testPoolFromMarketThenCurrency() {
        XCTAssertEqual(AssetMarket.pool(market: "US", ticker: "GOOG", currency: "USD"), .overseas)
        XCTAssertEqual(AssetMarket.pool(market: "CN", ticker: nil, currency: "CNY"), .domestic)
        XCTAssertEqual(AssetMarket.pool(market: nil, ticker: "000001", currency: "CNY"), .domestic)
        XCTAssertEqual(AssetMarket.pool(market: nil, ticker: "5555.SI", currency: "SGD"), .overseas)
        // 市场与代码都判断不出 → 币种回退
        XCTAssertEqual(AssetMarket.pool(market: "LSE", ticker: "VOD.L", currency: "CNY"), .domestic)
        XCTAssertEqual(AssetMarket.pool(market: nil, ticker: nil, currency: "SGD"), .overseas)
    }

    // MARK: - 税率必须锚定同一个市场结论

    func testTaxAnchoredToSameResolvedMarket() {
        let m = AssetMarket.resolve(market: nil, ticker: "BTC-USD")
        XCTAssertEqual(m, .us)
        XCTAssertEqual(DividendTax.rate(market: m?.rawValue, currency: "CNY"), DividendTax.usRate,
                       "池按市场判为境外，税率就必须按同一个市场判 10%，不能回退到 CNY 的 0%")
        XCTAssertEqual(DividendTax.rate(market: "nasdaq", currency: "CNY"), DividendTax.usRate)
        XCTAssertEqual(DividendTax.rate(market: "SH", currency: "USD"), DividendTax.cnRate)
        XCTAssertNil(DividendTax.rate(market: AssetMarket.jp.rawValue, currency: "JPY"))
        XCTAssertNil(DividendTax.rate(market: AssetMarket.sg.rawValue, currency: "SGD"))
    }

    // MARK: - 成本聚合（第 1 项需求）

    /// 三池成本 + 缺汇率按 1:1（与总市值同一处理方式）。
    func testAllocationCostAggregation() throws {
        let db = try makeDB("cost.db")
        try db.upsertFxRates([FxRate(currency: "USD", rateToCny: 7.0, asOfDate: "2026-09-30"),
                              FxRate(currency: "SGD", rateToCny: 5.0, asOfDate: "2026-09-30")])
        try db.upsertAssets([
            Asset(key: "000001", name: "境内基金", ticker: "000001", assetClass: "cn_fixed_income",
                  pool: .domestic, currency: "CNY"),
            Asset(key: "BBB", name: "美股ETF", ticker: "BBB", assetClass: "us_equity",
                  pool: .overseas, currency: "USD"),
            Asset(key: "5555.SI", name: "新加坡ETF", ticker: "5555.SI", assetClass: "sg_equity",
                  pool: .overseas, currency: "SGD"),
            // 没有汇率、也没有可推断的市场 → 成本按 1:1，池按币种（非 CNY → 境外）
            Asset(key: "VOD.L", name: "伦敦", ticker: "VOD.L", assetClass: "other",
                  pool: .overseas, currency: "GBP"),
        ])
        try db.upsertHoldings([
            Holding(assetKey: "000001", quantity: 100, costBasis: 1000, currency: "CNY", asOfDate: "2026-09-30"),
            Holding(assetKey: "BBB", quantity: 2, costBasis: 100, currency: "USD", asOfDate: "2026-09-30"),
            Holding(assetKey: "5555.SI", quantity: 1, costBasis: 1, currency: "SGD", asOfDate: "2026-09-30"),
            Holding(assetKey: "VOD.L", quantity: 10, costBasis: 20, currency: "GBP", asOfDate: "2026-09-30"),
        ])
        try db.upsertQuotes([
            Quote(symbol: "000001", price: 1.0, currency: "CNY", date: "2026-09-30", source: "test"),
            Quote(symbol: "BBB", price: 10, currency: "USD", date: "2026-09-30", source: "test"),
            Quote(symbol: "5555.SI", price: 1, currency: "SGD", date: "2026-09-30", source: "test"),
            Quote(symbol: "VOD.L", price: 2, currency: "GBP", date: "2026-09-30", source: "test"),
        ])

        let alloc = try Repository(db: db).fetchAllocation()
        // 成本：1000(CNY) + 700(USD×7) + 5(SGD×5) + 20(GBP 缺汇率按 1:1)
        XCTAssertEqual(alloc.totalCost, 1725, accuracy: 1e-9)
        XCTAssertEqual(alloc.domesticCost, 1000, accuracy: 1e-9)
        XCTAssertEqual(alloc.overseasCost, 725, accuracy: 1e-9)
        XCTAssertEqual(alloc.domesticCost + alloc.overseasCost, alloc.totalCost, accuracy: 1e-9,
                       "池只有境内/境外两种，两者必须等于总量")
        // 市值：100 + 140 + 5 + 20
        XCTAssertEqual(alloc.totalValue, 265, accuracy: 1e-9)
        XCTAssertEqual(alloc.domesticValue, 100, accuracy: 1e-9)
        XCTAssertEqual(alloc.overseasValue, 165, accuracy: 1e-9)
    }

    // MARK: - 读时派生：落库的 pool 不再有话语权（Q31）

    func testStoredPoolColumnIsNotAuthoritative() throws {
        let db = try makeDB("stale.db")
        // 故意造一个「落库 pool=境内、市场却是美国」的脏数据
        try db.upsertAssets([
            Asset(key: "GOOG", name: "谷歌", ticker: "GOOG", market: .us,
                  assetClass: "us_equity", pool: .domestic, currency: "USD"),
        ])
        try db.upsertHoldings([
            Holding(assetKey: "GOOG", quantity: 1, costBasis: 10, currency: "USD", asOfDate: "2026-09-30"),
        ])
        try db.upsertQuotes([
            Quote(symbol: "GOOG", price: 100, currency: "USD", date: "2026-09-30", source: "test"),
        ])
        let repo = Repository(db: db)
        let row = try XCTUnwrap(repo.fetchAssetPerspectives().first)
        XCTAssertEqual(row.market, .us)
        XCTAssertEqual(row.pool, .overseas, "池必须由市场派生，不能被落库的脏值带走")
        XCTAssertEqual(try XCTUnwrap(repo.fetchAllocation()).overseasValue, 100, accuracy: 1e-9)
        XCTAssertEqual(try XCTUnwrap(repo.fetchAllocation()).domesticValue, 0, accuracy: 1e-9)
        // 读回来的 Asset.pool 也应是派生值
        XCTAssertEqual(try XCTUnwrap(db.fetchAssets().first).pool, .overseas)
    }

    // MARK: - 归一化：清掉已废除的 'cross' 与自由文本市场

    func testNormalizeRewritesLegacyCrossAndFreeTextMarket() throws {
        let db = try makeDB("normalize.db")
        // 绕过 upsertAssets，直接写入旧版数据（pool='cross'、market 是自由文本）
        try db.exec("""
        INSERT INTO assets(key, name, ticker, market, asset_class, pool, currency, source, fee_rate, sort_order)
        VALUES('GOOG','谷歌','GOOG','nasdaq','us_equity','cross','USD','numbers',0,0)
        """)
        try db.exec("""
        INSERT INTO assets(key, name, ticker, market, asset_class, pool, currency, source, fee_rate, sort_order)
        VALUES('001234.CN_FUND','QDII','001234.CN_Fund','','greater_cn_equity','cross','CNY','numbers',0,1)
        """)
        try db.exec("""
        INSERT INTO assets(key, name, ticker, market, asset_class, pool, currency, source, fee_rate, sort_order)
        VALUES('VOD.L','伦敦','VOD.L','LSE','other','overseas','GBP','numbers',0,2)
        """)

        try db.normalizeAssetClassification()

        let rows = try db.fetchAssets()
        let goog = try XCTUnwrap(rows.first { $0.key == "GOOG" })
        XCTAssertEqual(goog.market, .us, "自由文本 nasdaq 必须归一成闭集里的 US")
        XCTAssertEqual(goog.pool, .overseas)
        let qdii = try XCTUnwrap(rows.first { $0.key == "001234.CN_FUND" })
        XCTAssertEqual(qdii.market, .cn)
        XCTAssertEqual(qdii.pool, .domestic, "空 market 按 ticker 推断（.CN_FUND → 中国内地）")
        let vod = try XCTUnwrap(rows.first { $0.key == "VOD.L" })
        XCTAssertNil(vod.market, "不在闭集内且推断不出 → NULL，不保留原文")
        XCTAssertEqual(vod.pool, .overseas, "市场缺失时按币种回退（GBP → 境外）")

        // 幂等：再跑一次不应有任何变化
        try db.normalizeAssetClassification()
        XCTAssertEqual(try db.fetchAssets().map(\.key).sorted(), ["001234.CN_FUND", "GOOG", "VOD.L"])
        XCTAssertEqual(try XCTUnwrap(db.fetchAssets().first { $0.key == "GOOG" }).market, .us)
    }

    /// 旧备份里的 `pool: "cross"` 与自由文本 market 由导入路径重新解析（Q20=C 的兼容面）。
    func testLegacyBackupImportDropsCrossPool() throws {
        let db = try makeDB("legacy-import.db")
        let json = """
        {
          "schema_version": 7,
          "assets": [
            {"key":"1111.HK","name":"示例港股ETF","ticker":"1111.HK","market":"HKEX",
             "asset_class":"greater_cn_equity","pool":"cross","currency":"HKD"},
            {"key":"159934.SZ","name":"易方达黄金ETF","ticker":"159934.SZ","market":"",
             "asset_class":"gold","pool":"cross","currency":"CNY"}
          ],
          "holdings": []
        }
        """
        let url = tmpDir.appendingPathComponent("legacy.json")
        try Data(json.utf8).write(to: url)
        try BackupManager(db: db, backupDir: tmpDir).importJSON(from: url)

        let rows = try db.fetchAssets()
        let hk = try XCTUnwrap(rows.first { $0.key == "1111.HK" })
        XCTAssertEqual(hk.market, .hk)
        XCTAssertEqual(hk.pool, .overseas)
        let gold = try XCTUnwrap(rows.first { $0.key == "159934.SZ" })
        XCTAssertEqual(gold.market, .cn, "混币种的「黄金」表拆开后，A股那只落到境内")
        XCTAssertEqual(gold.pool, .domestic)
    }
}
