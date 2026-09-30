import XCTest
@testable import PortfolioCore

/// 联网端到端实测 (默认跳过, 不在常规 CI 里跑网络).
///
///     PM_LIVE_TESTS=1 swift test --disable-sandbox --filter LiveDividend
///
/// 用**真实库的只读副本**(拷贝到临时目录, 绝不写原库)跑一遍:
/// 真实数据源抓股息 → Database 落库 → Repository 聚合, 并把每个资产的结果打印出来。
/// 它覆盖 DataSource / Database / Repository 三层; AppStore 的调度层就是
/// 「建 job → TaskGroup 限流并发 → 串行批量落库」, 逻辑与这里一致。
final class LiveDividendPipelineTests: XCTestCase {

    func testLiveDividendPipelineOnRealPortfolioCopy() async throws {
        try XCTSkipUnless(ProcessInfo.processInfo.environment["PM_LIVE_TESTS"] == "1",
                          "联网实测: 设置 PM_LIVE_TESTS=1 才会执行")
        let real = URL(fileURLWithPath: NSHomeDirectory())
            .appendingPathComponent("Library/Application Support/PortfolioManager/portfolio.db")
        try XCTSkipUnless(FileManager.default.fileExists(atPath: real.path), "找不到真实库")

        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("pm-live-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tmp) }
        let copy = tmp.appendingPathComponent("portfolio.db")
        try FileManager.default.copyItem(at: real, to: copy)

        let db = try Database(path: copy.path)
        XCTAssertEqual(db.currentSchemaVersion(), Schema.version, "v7 真实库副本应被迁移到 v8")

        let assets = try db.fetchAssets()
        let nowYear = Calendar.current.component(.year, from: Date())
        let years = Array(2024...nowYear)

        var updates: [(assetKey: String, records: [DividendRecord], meta: DividendFetchMeta)] = []
        for a in assets {
            let ticker = a.ticker ?? a.key
            let source: any DataSource
            let symbol: String
            if let code = Self.fundCode(ticker) {
                source = EastmoneySource(); symbol = code
            } else {
                source = YahooFinanceSource(); symbol = ticker
            }
            var records: [DividendRecord] = []
            let status: DividendFetchStatus
            do {
                let events = try await source.fetchDividends(symbol: symbol, years: years)
                records = events.map { DividendRecord(assetKey: a.key, exDate: $0.exDate,
                                                      amount: $0.amount, currency: $0.currency,
                                                      source: source.name) }
                status = .ok
            } catch {
                status = (error as? DataSourceError)?.isUnavailable == true ? .unavailable : .failed
            }
            updates.append((assetKey: a.key, records: records,
                            meta: DividendFetchMeta(assetKey: a.key, status: status,
                                                    source: source.name,
                                                    fetchedAt: "2026-09-29 13:00:00")))
        }
        try db.replaceDividendsBatch(updates)

        let repo = Repository(db: db)
        let summary = try repo.fetchDividendSummary()
        let rows = try repo.fetchAssetPerspectives()

        print("\n===== 全资产股息实测 (\(years.first!)–\(years.last!)) =====")
        print(String(format: "全资产股息 = ¥%.2f | 总市值 = ¥%.0f | 总成本 = ¥%.0f | 股息率 = %.2f%% | 覆盖 %d/%d",
                     summary.totalNetCny, try repo.fetchAllocation().totalValue,
                     try repo.fetchAllocation().totalCost,
                     summary.totalNetCny / max(1, try repo.fetchAllocation().totalValue) * 100,
                     summary.coveredCount, summary.assetCount))
        for row in rows {
            guard let d = row.dividend else { continue }
            let ps = d.status == .ok ? String(format: "%.4f/%.4f", d.perSharePrevPrev, d.perSharePrev) : "—"
            let est = d.estimatedTotal.map { String(format: "%.4f", $0) } ?? "—"
            let estCny = d.estimatedTotalCny.map { String(format: "%.2f", $0) } ?? "—"
            print(String(format: "%-18@ %-9@ 每股(前/去)=%-18@ 预计原币=%-12@ 折¥=%-10@ 已发生¥=%-9@ 税率=%@",
                         row.assetKey as NSString, d.status.rawValue as NSString,
                         ps as NSString, est as NSString, estCny as NSString,
                         (d.actualYtdCny.map { String(format: "%.2f", $0) } ?? "—") as NSString,
                         DividendTax.label(d.taxRate) as NSString))
        }
        print("===== 结束 =====\n")

        // 真实组合里必须有能算出来的股息 (否则说明抓取链路坏了).
        XCTAssertGreaterThan(summary.totalNetCny, 0)
        XCTAssertGreaterThanOrEqual(summary.coveredCount, 1)
    }

    /// 与 AppStore.resolveDataSource 同规则: 6 位基金代码 → 天天基金, 其余 → Yahoo.
    private static func fundCode(_ ticker: String) -> String? {
        let pattern = #"^(\d{6})(\.(CN_Fund|SZ|SH))?$"#
        guard let re = try? NSRegularExpression(pattern: pattern) else { return nil }
        let ns = ticker as NSString
        let m = re.firstMatch(in: ticker, range: NSRange(location: 0, length: ns.length))
        guard let m, m.numberOfRanges > 1, m.range(at: 1).location != NSNotFound else { return nil }
        return ns.substring(with: m.range(at: 1))
    }
}
