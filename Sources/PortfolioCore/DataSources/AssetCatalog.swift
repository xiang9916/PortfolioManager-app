public enum SourceKind: String, Codable, Hashable, Sendable { case yahoo, fund }

public struct AssetRef: Codable, Hashable, Sendable {
    public let key: String
    public let source: SourceKind
    public let symbol: String
    public let name: String?

    public init(_ key: String, _ source: SourceKind, _ symbol: String, name: String? = nil) {
        self.key = key; self.source = source; self.symbol = symbol; self.name = name
    }
}

/// 资产目录 —— 两层的唯一事实来源：
///
/// - 第 1 层 modeled 与 Python 的候选目录（Optimizer/scripts/market_data.py 的 SOURCES）一一对应。
///   改这里必须同步改 Python，并跑 Optimizer/tests/test_asset_catalog_parity.py。
/// - 第 2 层 legacy 是非模型的旧 key / 取数别名，只为本机已有资产与旧备份保留；
///   它不参与契约测试，也不代表优化器认识这些 key。
public enum AssetCatalog {
    /// 与 Python SOURCES 一一对应（46 项）。新增/改名/换代码必须两侧同时改。
    public static let modeled: [AssetRef] = [
        AssetRef("D_HSBC_SP500", .fund, "050025", name: "汇丰标普500"),
        AssetRef("D_HSBC_CDB", .fund, "007485", name: "汇丰国开债"),
        AssetRef("D_HSBC_MMF", .fund, "000891", name: "汇丰货币"),
        AssetRef("D_CN_CREDIT_BOND", .fund, "110035", name: "易方达双债增强A"),
        AssetRef("D_CN_QDII_STOCK", .fund, "000043", name: "QDII全球股票"),
        AssetRef("D_CN_QDII_STABLE", .fund, "017970", name: "QDII稳健配置"),
        AssetRef("D_CN_QDII_COMMODITY", .yahoo, "CL=F", name: "QDII商品/原油"),
        AssetRef("D_GREATER_CN", .yahoo, "2800.HK", name: "大中华权益"),
        AssetRef("O_US_CORE", .yahoo, "SPY", name: "美国核心"),
        AssetRef("O_BTC", .yahoo, "BTC-USD", name: "比特币"),
        AssetRef("O_HYLB", .yahoo, "HYLB", name: "高收益债"),
        AssetRef("O_JP_EQ", .yahoo, "1698.T", name: "日本权益"),
        AssetRef("O_SG_EQ", .yahoo, "G3B.SI", name: "新加坡权益"),
        AssetRef("O_US_TLT", .yahoo, "TLH", name: "美长债"),
        AssetRef("O_US_REIT", .yahoo, "REZ", name: "美REIT"),
        AssetRef("O_US_ENERGY", .yahoo, "XOM", name: "美能源"),
        AssetRef("O_IALT", .yahoo, "IALT", name: "系统化另类多策略"),
        AssetRef("O_GOLD", .yahoo, "GC=F", name: "黄金"),
        // 标普500 宽基这一档用 SPY（原写 SPLG，发行方改了代码后 Yahoo 一律 404）
        AssetRef("SPY", .yahoo, "SPY", name: "SPDR S&P 500 ETF Trust"),
        AssetRef("VTV", .yahoo, "VTV"),
        AssetRef("SPMO", .yahoo, "SPMO", name: "Invesco S&P 500 Momentum ETF"),
        AssetRef("UNH", .yahoo, "UNH", name: "UnitedHealth Group"),
        AssetRef("GOOG", .yahoo, "GOOG", name: "Alphabet"),
        AssetRef("AAPL", .yahoo, "AAPL"), AssetRef("MSFT", .yahoo, "MSFT"),
        AssetRef("NVDA", .yahoo, "NVDA"), AssetRef("AMZN", .yahoo, "AMZN"),
        AssetRef("META", .yahoo, "META"), AssetRef("TSLA", .yahoo, "TSLA"),
        // BUFFER 是四只 Max Buffer 的等权篮子（ADR 0006）；这里保留 MAXJ 作为取数代理，
        // 模型序列不是它，而是四只共同窗口的合成序列。
        AssetRef("BUFFER", .yahoo, "MAXJ", name: "缓冲期权"),
        AssetRef("1364", .yahoo, "1364.T"), AssetRef("1698", .yahoo, "1698.T"),
        AssetRef("2516", .yahoo, "2516.T"), AssetRef("1477", .yahoo, "1477.T"),
        AssetRef("1478", .yahoo, "1478.T"), AssetRef("1490", .yahoo, "1490.T"),
        AssetRef("2529", .yahoo, "2529.T"),
        AssetRef("CSI300", .fund, "110020"), AssetRef("DIVLOWVOL", .fund, "008163"),
        AssetRef("DIVLOWVOL100", .fund, "021550"), AssetRef("STOCK", .fund, "005225"),
        AssetRef("MIXED", .fund, "004604"), AssetRef("INDEX", .fund, "011608"),
        AssetRef("HK_BROAD", .yahoo, "2800.HK"), AssetRef("HK_DIV", .yahoo, "3031.HK"),
        AssetRef("HK_TECH", .yahoo, "3067.HK"),
    ]

    /// 非模型的旧 key / 取数别名（19 项）。保留它们是为了让已存在的本地资产仍能刷新行情；
    /// 不要让新增的模型标的落在这里 —— 模型标的必须进 modeled 并同步 Python。
    public static let legacy: [AssetRef] = [
        AssetRef("D_HSBC_CSI300", .fund, "000051", name: "汇丰沪深300"),
        AssetRef("D_HSBC_DIVLOWVOL", .fund, "007605", name: "汇丰红利低波"),
        AssetRef("D_HSBC_GOLD", .fund, "000307", name: "汇丰黄金"),
        AssetRef("D_HSBC_HSTECH", .fund, "013402", name: "汇丰恒生科技"),
        AssetRef("D_CN_DIVLOWVOL100", .fund, "021550", name: "中证红利低波100"),
        AssetRef("D_CN_STOCK", .fund, "000251", name: "主动股票"),
        AssetRef("D_CN_MIXED", .fund, "000849", name: "混合"),
        AssetRef("D_CN_INDEX", .fund, "001237", name: "其他指数"),
        AssetRef("D_CN_HK", .fund, "005698", name: "港股"),
        AssetRef("D_CN_QDII_GLOBAL", .fund, "000043", name: "QDII全球"),
        AssetRef("O_HK_HSTECH", .yahoo, "3067.HK", name: "港股科技"),
        AssetRef("O_HK_HIGHDIV", .yahoo, "3031.HK", name: "港股高股息"),
        AssetRef("O_HK_GOLD", .yahoo, "3170.HK", name: "港股黄金"),
        AssetRef("2800", .yahoo, "2800.HK"), AssetRef("2828", .yahoo, "2828.HK"),
        AssetRef("3031", .yahoo, "3031.HK"), AssetRef("3067", .yahoo, "3067.HK"),
        AssetRef("3110", .yahoo, "3110.HK"), AssetRef("3070", .yahoo, "3070.HK"),
    ]

    /// 两层合并后的完整查询表（保持旧 API；顺序 = modeled + legacy）。
    public static let refs: [AssetRef] = modeled + legacy

    private static let byKey: [String: AssetRef] = {
        var index: [String: AssetRef] = [:]
        for ref in refs { index[ref.key] = ref }
        return index
    }()

    public static func ref(for key: String) -> AssetRef? { byKey[key] }
}
