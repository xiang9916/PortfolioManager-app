import Foundation

/// 比较基准线的合成（能力1）：沪深300 + 标普500 按实时境内/境外池权重加权。
///
/// 纯函数（不联网、不读写库）：调用方负责取两条历史序列，这里只做
/// 「并集 + 前向填充 + 各自归一到 1.0 + 加权 + 裁到近 N 年 + 再归一」。
/// 抽出来的理由：这 40 行原本内联在 `AppStore.fetchBenchmark()` 里，App 层无法单测。
public enum BenchmarkSeries {

    /// - Parameters:
    ///   - spy: 标普500 序列（升序，收盘价口径由调用方决定）。
    ///   - csi: 沪深300 序列（升序）。为空 = 该腿缺失，按 1.0 常量参与加权。
    ///   - domesticWeight / overseasWeight: 池权重（二者通常归一）。
    ///   - lookbackYears: 裁到最近 N 年（与原实现一致，默认 3）。
    public static func compose(spy: [PricePoint], csi: [PricePoint],
                               domesticWeight: Double, overseasWeight: Double,
                               lookbackYears: Double = 3,
                               now: Date = Date(),
                               calendar: Calendar = .current) -> [PerformancePoint] {
        guard !spy.isEmpty || !csi.isEmpty else { return [] }

        var dateSet = Set<String>()
        var spyMap: [String: Double] = [:]
        var csiMap: [String: Double] = [:]
        if let base = spy.first?.close, base > 0 {
            for p in spy { spyMap[p.date] = p.close / base; dateSet.insert(p.date) }
        }
        if let base = csi.first?.close, base > 0 {
            for p in csi { csiMap[p.date] = p.close / base; dateSet.insert(p.date) }
        }
        let dates = dateSet.sorted()
        var lastSpy = 1.0, lastCsi = 1.0
        var allPoints: [PerformancePoint] = []
        for d in dates {
            if let v = spyMap[d] { lastSpy = v }
            if let v = csiMap[d] { lastCsi = v }
            allPoints.append(PerformancePoint(date: d,
                                              value: lastCsi * domesticWeight + lastSpy * overseasWeight))
        }

        // 裁到最近 N 年；再把裁剪后的首点归一，让基准线与组合线同起点。
        guard let cutoff = calendar.date(byAdding: .year, value: -Int(lookbackYears), to: now) else {
            return allPoints
        }
        let cutoffStr = SyncDates.utcDay(cutoff)
        var trimmed = allPoints.filter { $0.date >= cutoffStr }
        if let base = trimmed.first?.value, base > 0 {
            trimmed = trimmed.map { PerformancePoint(date: $0.date, value: $0.value / base) }
        }
        return trimmed
    }
}
