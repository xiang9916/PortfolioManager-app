import Foundation

/// 一次「行情 + 股息」同步的计划：**取数目标由调用方决定**，同步器只管执行。
///
/// 抽到 Core 的理由（ADR 0011）：改造前它是 `AppStore.refreshPrices()` 里 130 行内联控制流
/// + 100 行文件作用域 helper，而 App 层没有 testTarget —— 连既有测试都只能把这套
/// 「建 job → TaskGroup 限流 → 串行批量落库」照抄一遍才能验证
/// （`Tests/PortfolioCoreTests/LiveDividendPipelineTests.swift`）。
public struct SyncPlan: Sendable {

    public struct Target: Sendable {
        public let assetKey: String
        public let source: any DataSource
        public let symbol: String
        /// 命中股息缓存 → 本次只抓行情。
        public let skipDividends: Bool

        public init(assetKey: String, source: any DataSource, symbol: String,
                    skipDividends: Bool) {
            self.assetKey = assetKey
            self.source = source
            self.symbol = symbol
            self.skipDividends = skipDividends
        }
    }

    public let targets: [Target]
    /// 需要覆盖股息的自然年（含当年）。
    public let dividendYears: [Int]
    public let concurrency: Int
    public let now: Date

    public init(targets: [Target], dividendYears: [Int], concurrency: Int = 5, now: Date = Date()) {
        self.targets = targets
        self.dividendYears = dividendYears
        self.concurrency = concurrency
        self.now = now
    }
}

/// 一次同步的结算（调用方据此拼用户可见文案，Core 不写 UI 文案）。
public struct SyncReport: Sendable {
    public let pricePointCount: Int
    public let quoteCount: Int
    public let dividendUpdateCount: Int
    /// 行情抓取失败**或**落库失败的标的。
    public let failedAssetKeys: [String]
    /// 每个标的写入的历史点数（诊断 / CLI 分行输出用）。
    public let pointCounts: [String: Int]

    public init(pricePointCount: Int, quoteCount: Int, dividendUpdateCount: Int,
                failedAssetKeys: [String], pointCounts: [String: Int] = [:]) {
        self.pricePointCount = pricePointCount
        self.quoteCount = quoteCount
        self.dividendUpdateCount = dividendUpdateCount
        self.failedAssetKeys = failedAssetKeys
        self.pointCounts = pointCounts
    }
}

/// 行情 + 股息同步（能力1）。
///
/// 两条不可退让的既有约束（决策 Q31/Q33/Q34）：
/// 1. 网络阶段**并发**（限流），但子任务只 fetch、**不碰数据库**；
/// 2. 落库阶段回到调用方（MainActor）**串行批量写** —— `Database` 没有队列/锁，
///    并发写 SQLite 会 `SQLITE_BUSY` / 事务交错。
@MainActor
public enum MarketDataSync {

    /// 股息抓取覆盖的自然年起点（决策 Q20/Q26: 2024-01-01 至今）。
    nonisolated public static let dividendStartYear = 2024
    /// 股息缓存有效期 7 天（决策 Q17/Q24）。
    nonisolated public static let cacheTTL: TimeInterval = 7 * 86_400

    /// 需要覆盖的自然年：2024 ... 今年。
    nonisolated public static func dividendYears(now: Date = Date(),
                                                calendar: Calendar = .current) -> [Int] {
        let y = calendar.component(.year, from: now)
        guard y >= dividendStartYear else { return [y] }
        return Array(dividendStartYear...y)
    }

    /// 缓存新鲜：7 天内 **且** 与当前同一自然年 —— 跨年后目标年份变化, 必须重抓,
    /// 否则「去年」会缺最新一年（决策 Q24）。
    nonisolated public static func cacheIsFresh(_ meta: DividendFetchMeta,
                                               now: Date = Date(),
                                               calendar: Calendar = .current) -> Bool {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd HH:mm:ss"
        f.timeZone = calendar.timeZone
        guard let d = f.date(from: meta.fetchedAt) else { return false }
        guard calendar.component(.year, from: d) == calendar.component(.year, from: now) else { return false }
        return now.timeIntervalSince(d) < cacheTTL
    }

    /// 抓取时间戳（本地时区，"yyyy-MM-dd HH:mm:ss"）。
    nonisolated public static func stamp(now: Date, calendar: Calendar = .current) -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd HH:mm:ss"
        f.timeZone = calendar.timeZone
        return f.string(from: now)
    }

    /// 执行一次同步。返回结算；失败只记录、不中断（与改造前一致）。
    public static func run(_ plan: SyncPlan, into db: Database) async -> SyncReport {
        guard !plan.targets.isEmpty else {
            return SyncReport(pricePointCount: 0, quoteCount: 0,
                              dividendUpdateCount: 0, failedAssetKeys: [])
        }
        var pointCounts: [String: Int] = [:]
        let jobs = plan.targets.map {
            RefreshJob(assetKey: $0.assetKey, source: $0.source,
                       symbol: $0.symbol, skipDividends: $0.skipDividends)
        }
        let years = plan.dividendYears

        // 网络阶段: 并发 (限流), 无数据库访问.
        let outcomes = await withTaskGroup(of: RefreshOutcome.self) { group -> [RefreshOutcome] in
            var collected: [RefreshOutcome] = []
            var next = 0
            let limit = min(max(plan.concurrency, 1), jobs.count)
            while next < limit {
                let job = jobs[next]; next += 1
                group.addTask { await runRefreshJob(job, years: years, now: plan.now) }
            }
            while let out = await group.next() {
                collected.append(out)
                if next < jobs.count {
                    let job = jobs[next]; next += 1
                    group.addTask { await runRefreshJob(job, years: years, now: plan.now) }
                }
            }
            return collected
        }

        // 落库阶段: 单线程批量写入.
        var updated = 0
        var failed: [String] = []
        var newQuotes: [Quote] = []
        var dividendUpdates: [(assetKey: String, records: [DividendRecord],
                               meta: DividendFetchMeta)] = []
        for out in outcomes {
            var writeFailed = false
            updated += out.points.count
            pointCounts[out.assetKey, default: 0] += out.points.count
            if !out.points.isEmpty {
                // 落库失败也要计入失败数 (与改造前 try db.upsertPrices 的语义一致).
                do { try db.upsertPrices(out.points) } catch { writeFailed = true }
            }
            if out.priceFailed || writeFailed { failed.append(out.assetKey) }
            if let q = out.quote { newQuotes.append(q) }
            if let status = out.dividendStatus {
                let records = (out.dividends ?? []).map {
                    DividendRecord(assetKey: out.assetKey, exDate: $0.exDate,
                                   amount: $0.amount, currency: $0.currency,
                                   source: out.dividendSource ?? "")
                }
                dividendUpdates.append((
                    assetKey: out.assetKey,
                    records: records,
                    meta: DividendFetchMeta(assetKey: out.assetKey, status: status,
                                            source: out.dividendSource,
                                            fetchedAt: out.dividendStamp ?? stamp(now: plan.now))))
            }
        }
        if !newQuotes.isEmpty { try? db.upsertQuotes(newQuotes) }
        if !dividendUpdates.isEmpty { try? db.replaceDividendsBatch(dividendUpdates) }

        return SyncReport(pricePointCount: updated,
                          quoteCount: newQuotes.count,
                          dividendUpdateCount: dividendUpdates.count,
                          failedAssetKeys: failed,
                          pointCounts: pointCounts)
    }
}

// MARK: - 汇率

public struct FxSyncReport: Sendable {
    public let updated: [String]
    public let failed: [String]

    public init(updated: [String], failed: [String]) {
        self.updated = updated
        self.failed = failed
    }
}

/// 汇率同步（能力1）：逐币种取「CCYCNY=X」的最新价并批量落库。
///
/// 币种清单由调用方给（App 取自资产明细的币种、空组合时退回四个常用币种）；
/// `lookup` 是唯一的网络面，测试用闭包替代。
@MainActor
public enum FxRateSync {
    public static func run(currencies: [String], now: Date, into db: Database,
                           lookup: (String) async throws -> Double?) async -> FxSyncReport {
        var newRates: [FxRate] = []
        var failed: [String] = []
        for ccy in currencies.sorted() {
            do {
                guard let price = try await lookup(ccy), price > 0 else {
                    failed.append(ccy)
                    continue
                }
                newRates.append(FxRate(currency: ccy, rateToCny: price,
                                       asOfDate: SyncDates.utcDay(now), source: "yahoo"))
            } catch {
                failed.append(ccy)
            }
        }
        if !newRates.isEmpty { try? db.upsertFxRates(newRates) }
        return FxSyncReport(updated: newRates.map { $0.currency }, failed: failed)
    }
}

/// 同步侧用到的日期口径（UTC "yyyy-MM-dd"，与 DB 里其它日期一致）。
public enum SyncDates {
    public static func utcDay(_ date: Date) -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.timeZone = TimeZone(identifier: "UTC")
        return f.string(from: date)
    }
}

// MARK: - 单标的抓取（文件作用域: 不继承 @MainActor, 可在子任务里执行）

private struct RefreshJob: Sendable {
    let assetKey: String
    let source: any DataSource
    let symbol: String
    /// 命中缓存 → 本次不抓股息 (行情照抓).
    let skipDividends: Bool
}

private struct RefreshOutcome: Sendable {
    let assetKey: String
    let points: [PricePoint]
    let quote: Quote?
    let priceFailed: Bool
    /// nil 的 dividendStatus = 命中缓存, 本次不写股息.
    let dividends: [DividendEvent]?
    let dividendStatus: DividendFetchStatus?
    let dividendSource: String?
    let dividendStamp: String?
}

/// 单标的网络抓取 (历史行情 + 最新价 + 股息), 不触碰数据库.
private func runRefreshJob(_ job: RefreshJob, years: [Int], now: Date) async -> RefreshOutcome {
    var points: [PricePoint] = []
    var quote: Quote?
    var priceFailed = false
    do {
        let hist = try await job.source.fetchHistory(symbol: job.symbol)
        // 必须带上 开/高/低 与原始价：只重建 close 会把日内价/真实现价丢掉，
        // 「特别关注」的影线与极值卡的真实价格就永远补不上。
        points = hist.map { PricePoint(assetKey: job.assetKey, date: $0.date,
                                       close: $0.close, rawClose: $0.rawClose,
                                       currency: $0.currency,
                                       open: $0.open, high: $0.high, low: $0.low) }
        if let q = try? await job.source.fetchQuote(symbol: job.symbol) {
            quote = Quote(symbol: job.assetKey, price: q.price, currency: q.currency,
                          date: q.date, source: q.source)
        }
    } catch {
        priceFailed = true
    }

    var dividends: [DividendEvent]?
    var status: DividendFetchStatus?
    var stamp: String?
    if !job.skipDividends {
        do {
            dividends = try await job.source.fetchDividends(symbol: job.symbol, years: years)
            status = .ok
        } catch {
            // empty = 数据源对该标的无历史覆盖 (如新上市标的) → NULL 态;
            // 其它错误 = 抓取失败 → 也是 NULL 态, 但已抓到记录保留不删.
            status = (error as? DataSourceError)?.isUnavailable == true ? .unavailable : .failed
        }
        stamp = status == nil ? nil : MarketDataSync.stamp(now: now)
    }

    return RefreshOutcome(assetKey: job.assetKey, points: points, quote: quote,
                          priceFailed: priceFailed, dividends: dividends,
                          dividendStatus: status,
                          dividendSource: status == nil ? nil : job.source.name,
                          dividendStamp: stamp)
}
