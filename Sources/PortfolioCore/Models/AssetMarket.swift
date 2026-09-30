import Foundation

/// 标的所属市场 —— **闭集**，只有五种（Q32）。
///
/// 它是「池归属」与「股息税率」共同的唯一事实来源：两者都必须从这个解析结果派生，
/// 不允许各自去看别的地方（见 `docs/adr/0002-remove-cross-pool.md`）。
/// `assets.market` 列里存的永远是这里的 `rawValue`，或 NULL（未设置）。
public enum AssetMarket: String, Codable, Hashable, CaseIterable, Sendable {
    case us = "US"
    case hk = "HK"
    case cn = "CN"
    case jp = "JP"
    case sg = "SG"

    public var displayName: String {
        switch self {
        case .us: return "美国"
        case .hk: return "香港"
        case .cn: return "中国内地"
        case .jp: return "日本"
        case .sg: return "新加坡"
        }
    }

    /// 界面展示: "美国 (US)".
    public var displayLabel: String { "\(displayName) (\(rawValue))" }

    /// 池归属：中国内地 → 境内；其余四个市场 → 境外。
    public var pool: Pool { self == .cn ? .domestic : .overseas }

    // MARK: - 归一化 (原文 → 闭集)

    /// 精确别名表。收录旧版自由文本里出现过的写法（含中英文），
    /// 与旧 `DividendTax` 的三张词表保持一一对应，不再各存一份。
    private static let aliases: [String: AssetMarket] = [
        // 美国
        "US": .us, "USA": .us, "U.S.": .us, "U.S": .us, "UNITED_STATES": .us, "US_MARKET": .us,
        "NASDAQ": .us, "NYSE": .us, "AMEX": .us, "ARCA": .us, "BATS": .us,
        "美国": .us, "美股": .us, "美": .us,
        // 香港
        "HK": .hk, "HKEX": .hk, "SEHK": .hk, "HONGKONG": .hk, "HONG_KONG": .hk,
        "香港": .hk, "港股": .hk, "港": .hk,
        // 中国内地
        "CN": .cn, "SH": .cn, "SZ": .cn, "SS": .cn, "SSE": .cn, "SZSE": .cn, "CHINA": .cn,
        "A": .cn, "ASHARE": .cn, "A_SHARE": .cn, "CN_FUND": .cn,
        "中国": .cn, "A股": .cn, "沪深": .cn, "境内": .cn,
        // 日本
        "JP": .jp, "JPX": .jp, "TSE": .jp, "TOKYO": .jp, "JAPAN": .jp,
        "日本": .jp, "日股": .jp, "日": .jp,
        // 新加坡
        "SG": .sg, "SGX": .sg, "SINGAPORE": .sg, "新加坡": .sg, "狮城": .sg,
    ]

    /// 复合写法的模糊匹配（"NYSE ARCA"、"SHANGHAI" 等）。精确别名优先。
    private static let fuzzy: [(needle: String, market: AssetMarket)] = [
        ("NASDAQ", .us), ("NYSE", .us), ("AMEX", .us), ("ARCA", .us),
        ("HKEX", .hk), ("SEHK", .hk),
        ("SHANGHAI", .cn), ("SHENZHEN", .cn),
        ("TOKYO", .jp), ("JAPAN", .jp), ("JPX", .jp),
        ("SINGAPORE", .sg), ("SGX", .sg),
    ]

    /// 把任意原文归一成闭集内的市场；不在闭集内 → `nil`（**不保留原文**，见 Q32）。
    public static func canonical(from raw: String?) -> AssetMarket? {
        guard let raw else { return nil }
        let t = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return nil }
        let u = t.uppercased()
        if let exact = aliases[u] { return exact }
        for (needle, market) in fuzzy where u.contains(needle) { return market }
        return nil
    }

    // MARK: - ticker 推断 (Q26=C 规则表)

    /// 从 ticker 的形态/后缀推断市场：
    /// `.CN_FUND` → CN │ `.HK` → HK │ `.T` → JP │ `.SI` → SG │ `.SZ`/`.SH`/`.SS` → CN
    /// │ `-USD` → US │ 纯 6 位数字 → CN │ 纯 ASCII 字母 → US │ 其余 → `nil`。
    public static func infer(ticker: String?) -> AssetMarket? {
        guard let ticker else { return nil }
        let t = ticker.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard !t.isEmpty else { return nil }
        if t.hasSuffix(".CN_FUND") { return .cn }
        if t.hasSuffix(".HK") { return .hk }
        if t.hasSuffix(".T") { return .jp }
        if t.hasSuffix(".SI") { return .sg }
        if t.hasSuffix(".SZ") || t.hasSuffix(".SH") || t.hasSuffix(".SS") { return .cn }
        if t.hasSuffix("-USD") { return .us }
        if t.count == 6, t.allSatisfy({ $0.isASCII && $0.isNumber }) { return .cn }
        if t.allSatisfy({ $0.isASCII && $0.isLetter }) { return .us }
        return nil
    }

    /// 解析顺序：已填的 market（归一化） → ticker 推断 → `nil`。
    /// 返回 `nil` 时调用方按**币种**回退（池：CNY→境内；税率：USD/HKD/CNY→对应税率，其余未配置）。
    public static func resolve(market: String?, ticker: String?) -> AssetMarket? {
        canonical(from: market) ?? infer(ticker: ticker)
    }

    /// 池归属（含币种回退）。UI 与统计一律走这里，不再读 `assets.pool` 列。
    public static func pool(market: String?, ticker: String?, currency: String) -> Pool {
        if let m = resolve(market: market, ticker: ticker) { return m.pool }
        return currency.uppercased() == "CNY" ? .domestic : .overseas
    }
}
