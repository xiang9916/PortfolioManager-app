import SwiftUI
import Charts
import PortfolioCore

/// 第 4 个模块：特别关注 —— 组合合成日 K（20 根）+ 当日涨幅/跌幅最高两只。
///
/// 口径唯一事实来源 = FocusComposer（ADR 0008）：
/// 各标的先把 开/高/低/收 除以自己的今收（1.00），再按当前市值权重逐项加权；
/// 基金没有日内价 → 开 = 昨收、收 = 今收、高/低 = 两者较大/较小。
struct FocusView: View {
    @Bindable var store: AppStore

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                content
            }
            .padding()
        }
        .navigationTitle("特别关注")
        .toolbar { GlobalToolbarContent(store: store) }
        .overlay(alignment: .top) { statusOverlay }
    }

    @ViewBuilder
    private var content: some View {
        if store.hideNumbers {
            // 隐私模式：整页替换为占位（与财务分析同一约定）。
            card { PrivacyPlaceholder().frame(height: 320) }
        } else if store.focusBoard.candles.isEmpty {
            emptyState
        } else {
            compositeCard
            picksRow
        }
    }

    // MARK: - 组合合成 K

    private var compositeCard: some View {
        let board = store.focusBoard
        return VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("所有资产合成走势").font(.headline)
                    Text("近 \(board.candles.count) 个交易日 · 每根 = 1 个交易日 · 收盘价蜡烛（含上下影线）")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer(minLength: 0)
                if let change = board.dayChange {
                    VStack(alignment: .trailing, spacing: 2) {
                        Text(DisplayFormat.pct(change, signed: true))
                            .font(.subheadline.weight(.semibold)).monospacedDigit()
                            .foregroundStyle(change >= 0 ? .green : .red)
                        if let date = board.lastDate {
                            Text("最后日期 \(date)").font(.caption2).foregroundStyle(.secondary)
                        }
                    }
                }
            }
            CandleChart(candles: board.candles, height: 300, compact: false, axis: .percent)
            Text("合成 = 各资产「开/高/低/收」先除以自己的今收（归一到 1.00），再按当前市值权重逐项加权；基金没有日内价 → 开 = 昨收、收 = 今收、高/低 = 两者较大/较小。百分比以今收为 0%（最新一根收盘恒在 0%）。")
                .font(.caption2).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
        .padding()
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 12))
    }

    // MARK: - 当日极值两只

    @ViewBuilder
    private var picksRow: some View {
        let gainer = store.focusBoard.gainer
        let loser = store.focusBoard.loser
        if gainer != nil || loser != nil {
            HStack(alignment: .top, spacing: 16) {
                if let gainer {
                    pickCard(title: "当日涨幅最高", pick: gainer, up: true)
                }
                if let loser {
                    pickCard(title: "当日跌幅最高", pick: loser, up: false)
                }
            }
        }
    }

    private func pickCard(title: String, pick: FocusPick, up: Bool) -> some View {
        let price = pick.price
        let decimals = DisplayFormat.priceDecimals(for: price?.candles.last?.close ?? 0)
        return Button {
            // 跨模块的意图：ContentView 切到资产明细，AssetPerspectiveView 消费后清空。
            store.pendingAssetSelection = pick.assetKey
        } label: {
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .top, spacing: 8) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(title).font(.caption).foregroundStyle(up ? .green : .red)
                        Text(pick.name).font(.subheadline.weight(.semibold)).lineLimit(1)
                        Text(pick.assetKey).font(.caption2).foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 0)
                    VStack(alignment: .trailing, spacing: 2) {
                        if let price, let last = price.candles.last {
                            // 真实现价：权益 = 交易所收盘价，基金 = 份额净值（不是复权价/累计净值）。
                            Text(CurrencyStyle.symbol(price.currency)
                                 + DisplayFormat.price(last.close, decimals: decimals))
                                .font(.subheadline.weight(.semibold)).monospacedDigit()
                        }
                        Text(DisplayFormat.pct(pick.dayChange, signed: true))
                            .font(.caption.weight(.semibold)).monospacedDigit()
                            .foregroundStyle(up ? .green : .red)
                        Text("最后日期 \(pick.lastDate)").font(.caption2).foregroundStyle(.secondary)
                    }
                }
                if let price {
                    CandleChart(candles: price.candles, height: 140, compact: true,
                                axis: .price(decimals: decimals))
                } else {
                    CandleChart(candles: pick.candles, height: 140, compact: true, axis: .percent)
                }
                Text(price == nil
                     ? "该标的历史价格还没回补真实现价，先按归一化百分比画；点右上角「更新行情」后按实际价格重画。"
                     : "坐标 = 真实现价（权益为交易所收盘价，基金为份额净值）。顶部涨跌幅是总回报口径（含分红/分派），除息日会让 K 线出现向下跳空 —— 那是真实价格，不是亏损。")
                    .font(.caption2).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Text("点击查看该标的明细").font(.caption2).foregroundStyle(.secondary)
            }
            .padding()
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.background.secondary, in: RoundedRectangle(cornerRadius: 12))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help("跳到「资产明细」并选中 \(pick.name)")
    }

    // MARK: - 空态 / 提示

    private var emptyState: some View {
        card {
            ContentUnavailableView {
                Label("还没有可用的历史价格", systemImage: "chart.xyaxis.line")
            } description: {
                Text("点右上角「更新行情」抓取公开行情。合成蜡烛需要每个标的有至少两根历史收盘价；日内开/高/低 会在这次抓取时一并补上，补齐前按「无影线」显示。")
            }
        }
    }

    private var statusOverlay: some View {
        Group {
            if let msg = store.statusMessage {
                Text(msg)
                    .font(.caption)
                    .padding(.horizontal, 12).padding(.vertical, 6)
                    .background(.ultraThinMaterial, in: Capsule())
                    .padding(.top, 8)
                    .task(id: msg) {
                        try? await Task.sleep(for: .seconds(3))
                        if store.statusMessage == msg { store.statusMessage = nil }
                    }
            }
        }
    }

    private func card<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        content()
            .padding()
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.background.secondary, in: RoundedRectangle(cornerRadius: 12))
    }
}

// MARK: - 蜡烛图

/// 蜡烛图的纵轴口径。
///
/// - `.percent`：归一化口径（1.00 = 今收 = 0%），合成卡用；
/// - `.price`：现实世界的真实价格（权益 = 收盘价、基金 = 份额净值），极值卡用。
///
/// 两者共用同一套 Mark 与悬停逻辑，只有显示单位/读数字符串不同 —— 不复制第二份实现。
private enum CandleAxis: Equatable {
    case percent
    case price(decimals: Int)

    var isPercent: Bool { self == .percent }
}

/// 20 根日 K。实体 = 开→收，影线 = 低→高；绿涨红跌（收 ≥ 开 为绿）。
/// X 轴用日期、Y 轴只留刻度文字（不画网格线）。
private struct CandleChart: View {
    let candles: [FocusCandle]
    let height: CGFloat
    let compact: Bool
    let axis: CandleAxis

    @State private var hoverIndex: Int?

    /// 单根蜡烛的最小视觉高度（显示单位）—— 十字星/平盘日也要画得出来。
    private var minSpan: Double {
        switch axis {
        case .percent:
            return 0.06                     // 百分点：沿用改造前的值，组合卡外观不变
        case .price:
            let values = candles.flatMap { [$0.high, $0.low] }
            let range = (values.max() ?? 0) - (values.min() ?? 0)
            return max(range * 0.02, abs(candles.last?.close ?? 1) * 0.001, 1e-9)
        }
    }

    private struct Bar: Identifiable {
        let id: String
        let date: Date
        /// 开/高/低/收（显示单位：百分点 or 真实价格）—— 悬停读数用这一组。
        let open: Double
        let high: Double
        let low: Double
        let close: Double
        /// 仅渲染用的实体/影线端点（把十字星撑到最小可见高度，不改读数）。
        let bodyLow: Double
        let bodyHigh: Double
        let wickLow: Double
        let wickHigh: Double
        let up: Bool
        /// 百分比口径的当日涨跌（归一化序列的前收基准）。
        let dayChange: Double?
        /// 价格口径的当日涨跌额（同一价格序列的前收基准，不与顶部总回报口径混淆）。
        let priceDelta: Double?
    }

    /// 归一化蜡烛 → 百分比（百分点），价格蜡烛 → 原样。
    private func display(_ candle: FocusCandle) -> (open: Double, high: Double, low: Double, close: Double) {
        switch axis {
        case .percent:
            return ((candle.open - 1) * 100, (candle.high - 1) * 100,
                    (candle.low - 1) * 100, (candle.close - 1) * 100)
        case .price:
            return (candle.open, candle.high, candle.low, candle.close)
        }
    }

    private var bars: [Bar] {
        var out: [Bar] = []
        out.reserveCapacity(candles.count)
        var previousClose: Double?      // 归一化收盘（百分比口径的前收）
        var previousDisplay: Double?    // 显示单位收盘（价格口径的前收）
        for candle in candles {
            let values = display(candle)
            let open = values.open, high = values.high, low = values.low, close = values.close
            // 渲染用的最小高度：不改变读数，只保证十字星/平盘日可见。
            let bodyMin = min(open, close)
            let bodyMax = max(open, close)
            let bodyLow = bodyMax - bodyMin >= minSpan ? bodyMin : (bodyMin + bodyMax) / 2 - minSpan / 2
            let bodyHigh = bodyMax - bodyMin >= minSpan ? bodyMax : bodyLow + minSpan
            out.append(Bar(id: candle.date,
                           date: AssetClassStyle.parseDate(candle.date),
                           open: open, high: high, low: low, close: close,
                           bodyLow: bodyLow, bodyHigh: bodyHigh,
                           wickLow: min(low, bodyLow), wickHigh: max(high, bodyHigh),
                           up: candle.close >= candle.open,
                           dayChange: previousClose.flatMap { $0 == 0 ? nil : candle.close / $0 - 1 },
                           priceDelta: previousDisplay.map { close - $0 }))
            previousClose = candle.close
            previousDisplay = close
        }
        return out
    }

    var body: some View {
        let data = bars
        // 实体宽度按可用宽度算（20 根 → 每根约 55%）：.ratio 在连续日期轴上会被画成发丝线。
        GeometryReader { geo in
            let plotWidth = max(120, geo.size.width - 64)
            let bodyWidth = max(5, min(24, plotWidth / CGFloat(max(1, data.count)) * 0.55))
            chart(data: data, bodyWidth: bodyWidth)
        }
        .frame(height: height)
        .onChange(of: candles) { _, _ in hoverIndex = nil }
    }

    private func chart(data: [Bar], bodyWidth: CGFloat) -> some View {
        Chart {
            ForEach(data) { bar in
                RuleMark(x: .value("日期", bar.date),
                         yStart: .value("低", bar.wickLow),
                         yEnd: .value("高", bar.wickHigh))
                    .foregroundStyle(bar.up ? Color.green : Color.red)
                    .lineStyle(StrokeStyle(lineWidth: 1))
                RectangleMark(x: .value("日期", bar.date),
                              yStart: .value("开", bar.bodyLow),
                              yEnd: .value("收", bar.bodyHigh),
                              width: .fixed(bodyWidth))
                    .foregroundStyle(bar.up ? Color.green : Color.red)
            }
        }
        .chartYScale(domain: yDomain(data))
        .chartXAxis {
            AxisMarks(values: .automatic(desiredCount: compact ? 2 : 5)) { _ in
                AxisValueLabel(format: .dateTime.month(.twoDigits).day(.twoDigits))
            }
        }
        .chartYAxis {
            AxisMarks(position: .leading, values: .automatic(desiredCount: compact ? 2 : 5)) { value in
                AxisValueLabel {
                    Text(axisLabel(value.as(Double.self) ?? 0))
                        .font(.caption2)
                }
            }
        }
        .chartLegend(.hidden)
        .chartOverlay { proxy in
            GeometryReader { geo in
                ZStack(alignment: .topLeading) {
                    Rectangle()
                        .fill(.clear)
                        .contentShape(Rectangle())
                        .onContinuousHover { phase in
                            switch phase {
                            case .active(let point):
                                hoverIndex = nearestIndex(to: point, proxy: proxy, geo: geo)
                            case .ended:
                                hoverIndex = nil
                            }
                        }
                    if let index = hoverIndex, data.indices.contains(index),
                       let plot = proxy.plotFrame,
                       let plotted = proxy.position(forX: data[index].date) {
                        let originX = geo[plot].origin.x
                        tooltip(data[index])
                            .offset(x: min(max(originX + plotted - 78, 4),
                                           max(4, geo.size.width - 160)),
                                    y: 4)
                            .allowsHitTesting(false)
                    }
                }
            }
        }
    }

    private func yDomain(_ data: [Bar]) -> ClosedRange<Double> {
        let low = data.map(\.wickLow).min() ?? -1
        let high = data.map(\.wickHigh).max() ?? 1
        let pad = max(0.08, (high - low) * 0.12)
        return (low - pad)...(high + pad)
    }

    private func nearestIndex(to point: CGPoint, proxy: ChartProxy, geo: GeometryProxy) -> Int? {
        guard let plot = proxy.plotFrame else { return nil }
        let x = point.x - geo[plot].origin.x
        guard let date = proxy.value(atX: x, as: Date.self) else { return nil }
        var best: (index: Int, distance: TimeInterval)?
        for (index, bar) in bars.enumerated() {
            let distance = abs(bar.date.timeIntervalSince(date))
            if best == nil || distance < best!.distance { best = (index, distance) }
        }
        return best?.index
    }

    private func tooltip(_ bar: Bar) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 6) {
                Text(String(bar.id.suffix(5))).font(.caption.weight(.semibold)).monospacedDigit()
                if axis.isPercent, let change = bar.dayChange {
                    // 百分比口径：沿用改造前的「当日涨跌 %」。
                    Text(DisplayFormat.pct(change, signed: true))
                        .font(.caption2).monospacedDigit()
                        .foregroundStyle(change >= 0 ? .green : .red)
                } else if !axis.isPercent, let delta = bar.priceDelta {
                    // 价格口径：显示同一价格序列的涨跌额（总回报 % 在卡头，避免两个口径混在一行）。
                    Text(Self.priceLabel(delta, decimals: priceDecimals, signed: true))
                        .font(.caption2).monospacedDigit()
                        .foregroundStyle(delta >= 0 ? .green : .red)
                }
            }
            Text("开 \(axisLabel(bar.open))　高 \(axisLabel(bar.high))")
                .font(.caption2).monospacedDigit()
            Text("低 \(axisLabel(bar.low))　收 \(axisLabel(bar.close))")
                .font(.caption2).monospacedDigit()
        }
        .padding(8)
        .frame(width: 156, alignment: .leading)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.secondary.opacity(0.25)))
    }

    /// 价格口径的小数位数（百分比口径不使用）。
    private var priceDecimals: Int {
        if case .price(let d) = axis { return d }
        return 2
    }

    private func axisLabel(_ value: Double) -> String {
        switch axis {
        case .percent:
            return Self.percentLabel(value)
        case .price(let decimals):
            return DisplayFormat.price(value, decimals: decimals)
        }
    }

    private static func percentLabel(_ percent: Double) -> String {
        String(format: "%+.1f%%", percent)
    }

    private static func priceLabel(_ value: Double, decimals: Int, signed: Bool = false) -> String {
        (signed && value >= 0 ? "+" : "") + DisplayFormat.price(value, decimals: decimals)
    }
}
