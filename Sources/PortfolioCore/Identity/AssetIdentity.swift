import Foundation

// MARK: - 资产身份（唯一入口）
//
// 「这个资产是谁」只在这里回答：代码规范化、市场、池、取数源。
// 口径见 docs/adr/0010-asset-identity.md。
// 本文件不碰数据库 / UI / 网络；调用方只消费这里产出的值。

/// 一次取数所需的「数据源 + 代码」。
public struct AssetFeed: Hashable, Sendable {
    public let kind: SourceKind
    public let symbol: String

    public init(kind: SourceKind, symbol: String) {
        self.kind = kind
        self.symbol = symbol
    }
}

/// 代码规范化：全仓唯一的「这是什么代码」规则。
///
/// 规则：去空白 → 大写 → 6 位境内基金代码去掉 .CN_FUND / .SZ / .SH 后缀（大小写不敏感）。
/// .SS 是 A 股 Yahoo 后缀，故意不在基金后缀表里（A 股继续走 Yahoo）。
public enum AssetTicker {
    private static let fundSuffixes = [".CN_FUND", ".SZ", ".SH"]

    /// 规范化后的代码（基金 = 6 位数字；其余 = 大写原文）。
    public static func canonical(_ raw: String?) -> String {
        guard let raw else { return "" }
        let upper = raw.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !upper.isEmpty else { return "" }
        if let code = fundCode(upper) { return code }
        return upper
    }

    /// 规范化后是 6 位境内基金代码 → 该代码；否则 nil。
    public static func fundCode(_ raw: String?) -> String? {
        guard let raw else { return nil }
        let upper = raw.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !upper.isEmpty else { return nil }
        for suffix in fundSuffixes where upper.hasSuffix(suffix) {
            let body = String(upper.dropLast(suffix.count))
            return isSixDigits(body) ? body : nil
        }
        return isSixDigits(upper) ? upper : nil
    }

    private static func isSixDigits(_ text: String) -> Bool {
        text.count == 6 && text.allSatisfy { $0.isASCII && $0.isNumber }
    }
}

/// 取数源解析：基金代码 → 天天基金；目录 key → 目录里的源；其余 → Yahoo。
public enum AssetFeedResolver {
    /// 解析顺序：effectiveTicker 的基金代码 → AssetCatalog.ref(for: key) → Yahoo(effectiveTicker)。
    /// 与旧 AppStore.resolveDataSource 的优先级一致，只是先做规范化。
    public static func feed(key: String, ticker: String?) -> AssetFeed? {
        let effective = effectiveTicker(key: key, ticker: ticker)
        if let code = AssetTicker.fundCode(effective) {
            return AssetFeed(kind: .fund, symbol: code)
        }
        if let ref = AssetCatalog.ref(for: key) {
            return AssetFeed(kind: ref.source, symbol: ref.symbol)
        }
        let canonical = AssetTicker.canonical(effective)
        return canonical.isEmpty ? nil : AssetFeed(kind: .yahoo, symbol: canonical)
    }

    /// 非空 ticker 优先；空 / 缺省按 key 兜底（资产 key 本身就是代码的旧数据）。
    static func effectiveTicker(key: String, ticker: String?) -> String {
        if let ticker {
            let trimmed = ticker.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty { return trimmed }
        }
        return key
    }
}

/// 一个资产的身份：规范化代码 + 市场 + 池 + 币种 + 取数源。
///
/// 池与市场的回退规则复用 AssetMarket（市场别名 / 币种回退只有那一份实现）。
public struct AssetIdentity: Hashable, Sendable {
    public let key: String
    public let ticker: String?
    public let canonicalTicker: String
    public let fundCode: String?
    public let market: AssetMarket?
    public let pool: Pool
    public let currency: String

    public init(key: String, ticker: String?, marketRaw: String?, currency: String) {
        self.key = key
        self.ticker = ticker
        self.currency = currency
        let effective = AssetFeedResolver.effectiveTicker(key: key, ticker: ticker)
        self.canonicalTicker = AssetTicker.canonical(effective)
        self.fundCode = AssetTicker.fundCode(effective)
        self.market = AssetMarket.resolve(market: marketRaw, ticker: effective)
        self.pool = AssetMarket.pool(market: marketRaw, ticker: effective, currency: currency)
    }

    public init(_ asset: Asset) {
        self.init(key: asset.key, ticker: asset.ticker,
                  marketRaw: asset.market?.rawValue, currency: asset.currency)
    }

    public var feed: AssetFeed? {
        AssetFeedResolver.feed(key: key, ticker: ticker)
    }

    /// 落库的 market 是否需要重写。
    ///
    /// 与旧实现「canonical vs upper+trim(原文)」不同：这里对原文做精确比较，
    /// 因此 us / 两端带空白 / 自由文本 / 空串都会收敛成 rawValue 或 NULL（幂等）。
    public static func marketNeedsRewrite(stored: String?, resolved: AssetMarket?) -> Bool {
        guard let stored else { return resolved != nil }
        let trimmed = stored.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty { return true }
        return resolved?.rawValue != stored
    }
}
