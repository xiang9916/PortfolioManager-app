import Foundation

/// 特别关注（第 4 个模块）：合成日 K 线的纯计算内核。
///
/// 口径唯一事实来源 = ADR 0008，规则只有四条：
/// 1. **归一化基准 = 各标的最新收盘（今收）= 1.00** —— 所以最新一根的收盘恒为 1.00。
/// 2. **合成 = 逐项加权**：组合的 开/高/低/收 分别 = Σ 权重 × (该资产同名字段 ÷ 该资产今收)。
/// 3. **无日内价**（基金净值、v9 之前抓的历史行）：开 = 昨收、收 = 今收、高/低 = 两者较大/较小。
/// 4. **当日无数据**（休市 / T+1 未更新）：平盘，开 = 高 = 低 = 收 = 最近的收盘 —— 不产生假影线。
///
/// 涨跌归因（窗口 / 逐标的贡献）见 FocusAttribution.swift 与 ADR 0013 —— 复用同一套
/// 归一化与前向填充口径，所以「当日」归因的组合涨跌与 dayChange 逐位一致。
///
/// 这里不做任何数据库访问，输入是已经取好的 PricePoint 序列，便于纯函数单测。
public struct FocusCandle: Hashable, Identifiable, Sendable {
    /// "yyyy-MM-dd"（与 prices.date 同口径）。
    public let date: String
    /// 归一化后的开/高/低/收：1.0 = 该标的（或组合）的今收。视图层负责换算成百分比。
    public let open: Double
    public let high: Double
    public let low: Double
    public let close: Double
    public var id: String { date }

    public init(date: String, open: Double, high: Double, low: Double, close: Double) {
        self.date = date
        self.open = open
        self.high = high
        self.low = low
        self.close = close
    }
}

/// 一只标的的**真实现价**序列（窗口内每根都有原始价时才存在）。
///
/// 与合成 K 完全不同口径：这里不归一化 —— 坐标就是现实世界的价格
/// （权益 = 交易所收盘价，基金 = 份额净值），用于极值卡的"真实价格"显示。
public struct FocusPrice: Hashable, Sendable {
    /// 报价币种（卡上显示货币符号）。
    public let currency: String
    public let candles: [FocusCandle]

    public init(currency: String, candles: [FocusCandle]) {
        self.currency = currency
        self.candles = candles
    }
}

/// 当日涨幅最高 / 跌幅最高的那只标的（角标 + 自己的 20 根日 K）。
public struct FocusPick: Hashable, Sendable {
    public let assetKey: String
    public let name: String
    /// 参与合成的权重（未归一，仅用于并列时的稳定排序）。
    public let weight: Double
    /// 当日涨跌 = 该标的**自己**最近两个观测点的收盘变化。
    public let dayChange: Double
    /// 该标的自己最后一根 bar 的日期（T+1 的基金会早于组合最新日期）。
    public let lastDate: String
    /// 归一化蜡烛（今收 = 1.00）—— 未回补真实现价时视图仍画这一组。
    public let candles: [FocusCandle]
    /// 真实现价蜡烛；nil = 窗口内至少一根没有原始价（视图退回百分比，不显示假价格）。
    public let price: FocusPrice?

    public init(assetKey: String, name: String, weight: Double, dayChange: Double,
                lastDate: String, candles: [FocusCandle], price: FocusPrice? = nil) {
        self.assetKey = assetKey
        self.name = name
        self.weight = weight
        self.dayChange = dayChange
        self.lastDate = lastDate
        self.candles = candles
        self.price = price
    }
}

public struct FocusBoard: Hashable, Sendable {
    public let candles: [FocusCandle]
    public let gainer: FocusPick?
    public let loser: FocusPick?
    /// 合成口径的当日涨跌（最后一根收 ÷ 前一根收 − 1）；不足两根为 nil。
    public let dayChange: Double?
    public let lastDate: String?
    /// 按窗口给出的涨跌归因（当日 / 近 5 日 / 近 20 日）；不足两根合成 K 时为空数组。
    public let attributions: [FocusAttribution]

    public init(candles: [FocusCandle], gainer: FocusPick?, loser: FocusPick?,
                dayChange: Double?, lastDate: String?,
                attributions: [FocusAttribution] = []) {
        self.candles = candles
        self.gainer = gainer
        self.loser = loser
        self.dayChange = dayChange
        self.lastDate = lastDate
        self.attributions = attributions
    }

    public func attribution(for window: FocusWindow) -> FocusAttribution? {
        attributions.first { $0.window == window }
    }

    public static let empty = FocusBoard(candles: [], gainer: nil, loser: nil,
                                        dayChange: nil, lastDate: nil, attributions: [])
}

/// 一只持仓的输入：价格序列（升序）+ 当前市值权重（折人民币，未归一）。
public struct FocusSeries: Hashable, Sendable {
    public let assetKey: String
    public let name: String
    public let weight: Double
    public let bars: [PricePoint]

    public init(assetKey: String, name: String, weight: Double, bars: [PricePoint]) {
        self.assetKey = assetKey
        self.name = name
        self.weight = weight
        self.bars = bars
    }
}

public enum FocusComposer {
    /// 固定 20 根：每根 = 1 个交易日（不做区间切换）。
    public static let defaultCandleCount = 20

    public static func compose(series: [FocusSeries],
                               candleCount: Int = defaultCandleCount) -> FocusBoard {
        let count = max(1, candleCount)
        // 少于两根 bar 的持仓无法定义「当日」也无法画实体：排除，并把权重在剩余持仓间重新归一
        // （权重在 makeCandles 里按 sumWeight 归一，所以这里只需要过滤）。
        let usable = series
            .filter { $0.weight > 0 && $0.bars.count >= 2 && $0.bars.contains { $0.close > 0 } }
            .map { FocusSeries(assetKey: $0.assetKey, name: $0.name, weight: $0.weight,
                               bars: $0.bars.sorted { $0.date < $1.date }) }
        guard !usable.isEmpty else { return .empty }

        // 组合日历 = 各序列日期的并集（升序），取最后 count 个 → 正好 count 根。
        var union = Set<String>()
        for s in usable { for b in s.bars { union.insert(b.date) } }
        let window = Array(union.sorted().suffix(count))
        let entries = usable.compactMap { entry(for: $0) }
        let candles = makeCandles(entries, dates: window)

        // 极值：只在「有两个以上观测点」的持仓里选；并列按权重降序取第一只。
        var ranked: [(series: FocusSeries, change: Double)] = []
        for s in usable {
            guard s.bars.count >= 2,
                  let last = s.bars.last,
                  let prev = s.bars.dropLast().last, prev.close > 0 else { continue }
            ranked.append((s, last.close / prev.close - 1))
        }
        ranked.sort { lhs, rhs in
            if lhs.change != rhs.change { return lhs.change > rhs.change }
            return lhs.series.weight > rhs.series.weight
        }
        let gainer = ranked.first.flatMap { pick(from: $0.series, change: $0.change, count: count) }
        // 只有一个可比标的时不给跌幅卡（避免同一只出现两次）。
        let loser = ranked.count >= 2
            ? ranked.last.flatMap { pick(from: $0.series, change: $0.change, count: count) }
            : nil

        let dayChange: Double? = {
            guard candles.count >= 2,
                  let last = candles.last, let prev = candles.dropLast().last,
                  prev.close != 0 else { return nil }
            return last.close / prev.close - 1
        }()

        return FocusBoard(candles: candles, gainer: gainer, loser: loser,
                          dayChange: dayChange, lastDate: candles.last?.date,
                          attributions: makeAttributions(entries, dates: window))
    }

    // MARK: - internals

    private struct Entry {
        let assetKey: String
        let name: String
        let bars: [PricePoint]
        /// 今收（归一化基准）。
        let base: Double
        let weight: Double
    }

    private static func entry(for series: FocusSeries) -> Entry? {
        guard let base = series.bars.last?.close, base > 0 else { return nil }
        return Entry(assetKey: series.assetKey, name: series.name,
                     bars: series.bars, base: base, weight: series.weight)
    }

    private static func pick(from series: FocusSeries, change: Double, count: Int) -> FocusPick? {
        guard let entry = entry(for: series), let last = series.bars.last else { return nil }
        let dates = series.bars.suffix(count).map { $0.date }
        return FocusPick(assetKey: series.assetKey, name: series.name, weight: series.weight,
                         dayChange: change, lastDate: last.date,
                         candles: makeCandles([entry], dates: dates),
                         price: priceSeries(for: series, count: count))
    }

    /// 真实现价序列（**全有或全无**）：窗口内任一根缺原始价就整体返回 nil。
    ///
    /// 混着画会让一根累计净值夹在单位净值中间，出现假尖峰 —— 宁可退回百分比。
    /// 每根按当日 `rawClose / close` 因子缩放：入库的 O/H/L 与 close 同一复权口径，
    /// 乘同一个因子就精确还原成原始 O/H/L（权益）；没有日内价（基金）时按 ADR 0008 第 4 条降级。
    private static func priceSeries(for series: FocusSeries, count: Int) -> FocusPrice? {
        let bars = series.bars
        guard !bars.isEmpty else { return nil }
        let start = max(0, bars.count - max(1, count))
        let window = Array(bars[start...])
        guard window.allSatisfy({ ($0.rawClose ?? 0) > 0 }) else { return nil }

        // 首根的「昨收」取窗口外那一根（存在且有原始价时），否则用自己的收盘。
        var previous: Double? = start > 0 ? bars[start - 1].rawClose : nil
        var out: [FocusCandle] = []
        out.reserveCapacity(window.count)
        for bar in window {
            let close = bar.rawClose ?? bar.close
            let scale = bar.close > 0 ? close / bar.close : 1
            let open: Double, high: Double, low: Double
            if let o = bar.open, let h = bar.high, let l = bar.low {
                open = o * scale; high = h * scale; low = l * scale
            } else {
                let o = previous ?? close
                open = o; high = max(o, close); low = min(o, close)
            }
            out.append(FocusCandle(date: bar.date, open: open, high: high, low: low, close: close))
            previous = close
        }
        return FocusPrice(currency: window.last?.currency ?? "CNY", candles: out)
    }

    /// 逐日合成：每个日期上，把各资产的开/高/低/收除以各自今收后按权重加权。
    private static func makeCandles(_ entries: [Entry], dates: [String]) -> [FocusCandle] {
        guard !entries.isEmpty else { return [] }
        var pointers = [Int](repeating: -1, count: entries.count)
        var out: [FocusCandle] = []
        out.reserveCapacity(dates.count)

        for day in dates {
            var sumOpen = 0.0, sumHigh = 0.0, sumLow = 0.0, sumClose = 0.0, sumWeight = 0.0
            for (i, entry) in entries.enumerated() {
                // 前向填充：指针停在「日期 ≤ day 的最后一根」。
                while pointers[i] + 1 < entry.bars.count && entry.bars[pointers[i] + 1].date <= day {
                    pointers[i] += 1
                }
                let p = pointers[i]
                // 该日期早于这只标的的第一根 bar → 当天不参与（权重按当天参与者归一），
                // 而不是用一个"未来的收盘"往前填出平盘。
                if p < 0 { continue }
                let bar = entry.bars[p]
                let isToday = bar.date == day
                let prevClose = p > 0 ? entry.bars[p - 1].close : bar.close

                let ohlc: (open: Double, high: Double, low: Double, close: Double)
                if isToday, let open = bar.open, let high = bar.high, let low = bar.low {
                    // 真实日内价（复权口径已在数据层换算好）。
                    ohlc = (open, high, low, bar.close)
                } else if isToday {
                    // 有收盘、无日内价：开 = 昨收，收 = 今收，高/低 = 两者较大/较小。
                    let open = prevClose
                    ohlc = (open, max(open, bar.close), min(open, bar.close), bar.close)
                } else {
                    // 当日无数据：平盘（不产生假影线）。
                    ohlc = (bar.close, bar.close, bar.close, bar.close)
                }

                let w = entry.weight
                sumOpen += w * (ohlc.open / entry.base)
                sumHigh += w * (ohlc.high / entry.base)
                sumLow += w * (ohlc.low / entry.base)
                sumClose += w * (ohlc.close / entry.base)
                sumWeight += w
            }
            guard sumWeight > 0 else { continue }
            out.append(FocusCandle(date: day,
                                   open: sumOpen / sumWeight,
                                   high: sumHigh / sumWeight,
                                   low: sumLow / sumWeight,
                                   close: sumClose / sumWeight))
        }
        return out
    }

    // MARK: - 涨跌归因（ADR 0013）

    /// 三个窗口逐个算；不足两根合成 K 时整体为空（无法定义区间）。
    private static func makeAttributions(_ entries: [Entry], dates: [String]) -> [FocusAttribution] {
        guard dates.count >= 2 else { return [] }
        return FocusWindow.allCases.compactMap { window in
            makeAttribution(entries, dates: dates, window: window)
        }
    }

    /// 一个窗口的精确分解。
    ///
    /// 期初 t0 = 窗口第一根；参与者 = 期初已有价格的标的（首根 bar ≤ t0），
    /// 期初收盘 c0 = 最近 ≤ t0 的收盘（前向填充，与合成 K 同一套指针语义）。
    /// 记 V(t0) = Σ w·c0/base ÷ Σ w（= 期初合成收盘），则
    ///
    /// - 组合区间涨跌 = 1/V(t0) − 1；
    /// - 期初有效权重 ω_i = w_i·(c0_i/base_i) ÷ Σ_j w_j·(c0_j/base_j)；
    /// - contribution_i = ω_i·(base_i/c0_i − 1)，且 Σ contribution_i ≡ 1/V(t0) − 1。
    ///
    /// 期初之后才出现的标的不进参与集：它没有期初权重，也就没有可归因的贡献。
    private static func makeAttribution(_ entries: [Entry], dates: [String],
                                        window: FocusWindow) -> FocusAttribution? {
        let bars = min(window.tradingBars, dates.count)
        guard bars >= 2 else { return nil }
        let windowDates = Array(dates.suffix(bars))
        guard let start = windowDates.first, let end = windowDates.last else { return nil }

        var participants: [(entry: Entry, c0: Double, stale: Bool)] = []
        for entry in entries {
            var index = -1
            while index + 1 < entry.bars.count && entry.bars[index + 1].date <= start {
                index += 1
            }
            guard index >= 0 else { continue }
            let c0 = entry.bars[index].close
            guard c0 > 0 else { continue }
            // stale = 期初之后**再没有**新观测（今收的日期不晚于期初）。
            let latestDate = entry.bars.last?.date ?? entry.bars[index].date
            participants.append((entry, c0, latestDate <= start))
        }
        guard !participants.isEmpty else { return nil }

        let sumWeight = participants.reduce(0.0) { $0 + $1.entry.weight }
        guard sumWeight > 0 else { return nil }
        // 期初合成收盘 × Σw：参与者的 w·c0/base 之和。
        let value = participants.reduce(0.0) { $0 + $1.entry.weight * ($1.c0 / $1.entry.base) }
        guard value > 0 else { return nil }
        let totalChange = sumWeight / value - 1

        var rows: [FocusContribution] = []
        rows.reserveCapacity(participants.count)
        for p in participants {
            let change = p.entry.base / p.c0 - 1
            let omega = p.entry.weight * (p.c0 / p.entry.base) / value
            rows.append(FocusContribution(assetKey: p.entry.assetKey,
                                          name: p.entry.name,
                                          change: change,
                                          weight: p.entry.weight / sumWeight,
                                          contribution: omega * change,
                                          stale: p.stale))
        }
        rows.sort { lhs, rhs in
            if lhs.contribution != rhs.contribution { return lhs.contribution > rhs.contribution }
            return lhs.assetKey < rhs.assetKey
        }
        let upside = rows.filter { $0.contribution > 0 }.reduce(0.0) { $0 + $1.contribution }
        let downside = rows.filter { $0.contribution < 0 }.reduce(0.0) { $0 + $1.contribution }
        let gross = rows.reduce(0.0) { $0 + abs($1.contribution) }
        let top3Share: Double? = gross > 0
            ? rows.map { abs($0.contribution) }.sorted(by: >).prefix(3).reduce(0.0, +) / gross
            : nil

        return FocusAttribution(window: window, startDate: start, endDate: end,
                                totalChange: totalChange,
                                upside: upside, downside: downside,
                                gainers: rows.filter { $0.contribution > 0 }.count,
                                losers: rows.filter { $0.contribution < 0 }.count,
                                contributions: rows, top3Share: top3Share)
    }
}
