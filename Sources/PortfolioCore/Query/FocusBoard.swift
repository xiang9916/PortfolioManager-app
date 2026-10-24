import Foundation

/// 特别关注（第 4 个模块）：合成日 K 线的纯计算内核。
///
/// 口径唯一事实来源 = ADR 0008，规则只有四条：
/// 1. **归一化基准 = 各标的最新收盘（今收）= 1.00** —— 所以最新一根的收盘恒为 1.00。
/// 2. **合成 = 逐项加权**：组合的 开/高/低/收 分别 = Σ 权重 × (该资产同名字段 ÷ 该资产今收)。
/// 3. **无日内价**（基金净值、v9 之前抓的历史行）：开 = 昨收、收 = 今收、高/低 = 两者较大/较小。
/// 4. **当日无数据**（休市 / T+1 未更新）：平盘，开 = 高 = 低 = 收 = 最近的收盘 —— 不产生假影线。
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

    public init(candles: [FocusCandle], gainer: FocusPick?, loser: FocusPick?,
                dayChange: Double?, lastDate: String?) {
        self.candles = candles
        self.gainer = gainer
        self.loser = loser
        self.dayChange = dayChange
        self.lastDate = lastDate
    }

    public static let empty = FocusBoard(candles: [], gainer: nil, loser: nil,
                                        dayChange: nil, lastDate: nil)
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
                          dayChange: dayChange, lastDate: candles.last?.date)
    }

    // MARK: - internals

    private struct Entry {
        let bars: [PricePoint]
        /// 今收（归一化基准）。
        let base: Double
        let weight: Double
    }

    private static func entry(for series: FocusSeries) -> Entry? {
        guard let base = series.bars.last?.close, base > 0 else { return nil }
        return Entry(bars: series.bars, base: base, weight: series.weight)
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
}
