import Foundation
import SwiftUI
import PortfolioCore

/// App-level paths. Dev layout points at the repo tree; packaged (.app) layout
/// relocates the DB + vendored optimizer into the bundle / Application Support.
public enum AppPaths {
    /// True when running inside a packaged .app bundle.
    public static var isBundled: Bool {
        Bundle.main.bundleURL.pathExtension == "app"
    }

    /// Application Support root (user-writable, survives app updates).
    public static func supportDir() -> URL {
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
        let dir = support.appendingPathComponent("PortfolioManager", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    /// Database location. Dev: repo tmp/portfolio.db if present; packaged: Application Support.
    public static func databaseURL() -> URL {
        if !isBundled {
            let dev = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
                .appendingPathComponent("tmp/portfolio.db")
            if FileManager.default.fileExists(atPath: dev.path) { return dev }
        }
        return supportDir().appendingPathComponent("portfolio.db")
    }

    /// Vendored optimizer scripts directory (packaged: bundle Resources, else repo).
    public static func scriptsURL() -> URL {
        if isBundled, let res = Bundle.main.resourceURL {
            return res.appendingPathComponent("Optimizer/scripts", isDirectory: true)
        }
        return URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
            .appendingPathComponent("Optimizer/scripts", isDirectory: true)
    }

    /// Python interpreter inside the vendored venv.
    /// Resolution order: env override → bundled venv → repo venv → PATH python3.
    public static func interpreterPath() -> String {
        if let env = ProcessInfo.processInfo.environment["PORTFOLIO_OPTIMIZER_PYTHON"] {
            return env
        }
        if isBundled, let res = Bundle.main.resourceURL {
            let bundled = res.appendingPathComponent("Optimizer/.venv/bin/python3").path
            if FileManager.default.isExecutableFile(atPath: bundled) { return bundled }
        }
        let dev = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
            .appendingPathComponent("Optimizer/.venv/bin/python3").path
        if FileManager.default.isExecutableFile(atPath: dev) { return dev }
        return "/usr/bin/env python3"
    }

    /// Extracted workbook state (input to the optimizer).
    public static func extractJSONURL() -> URL {
        if isBundled { return supportDir().appendingPathComponent("extract_app.json") }
        return URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
            .appendingPathComponent("tmp/extract_app.json")
    }

    /// live 覆盖后的提取文件（优化运行与敏感性分析共用同一个文件名）。
    /// 只有 `liveExtractURL()` 会写它；敏感性分析读它——两者必须指向同一个文件。
    public static func liveExtractURL() -> URL {
        extractJSONURL()
            .deletingLastPathComponent()
            .appendingPathComponent("extract_live.json")
    }

    /// 调 Python 子进程所需的三个不可变参数（优化运行 / 敏感性 / 宏观利率共用）。
    public static func sidecarConfig() -> SidecarConfig {
        SidecarConfig(interpreterPath: interpreterPath(),
                      scriptsDir: scriptsURL(),
                      currentDirectoryURL: URL(fileURLWithPath: FileManager.default.currentDirectoryPath))
    }

    /// Daily-backup directory (sibling of the active DB's parent).
    public static func backupsURL() -> URL {
        let parent = databaseURL().deletingLastPathComponent()
        let dir = parent.appendingPathComponent("backups", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }
}

/// Central @MainActor store: owns the DB + repository + optimizer and exposes
/// observable state to all SwiftUI views.
@MainActor
@Observable
public final class AppStore {
    /// 三个 Core 对象都是**私有**：视图只能通过快照与意图方法驱动 App（ADR 0011）。
    private let db: Database
    private let repository: Repository
    /// 只由 `OptimizationRunner` 使用；视图不再直接持有它。
    private let optimizer: OptimizationService

    // 模块1 / 能力1
    public var allocation: AllocationSnapshot?
    /// 组合估值的降级说明（缺价 / 缺汇率 / 币种不一致），供资产总览脚注展示。
    public var valuationWarnings: [ValuationWarning] = []
    public var performancePoints: [PerformancePoint] = []
    public var performanceSummary: PerformanceSummary?
    public var benchmarkPoints: [PerformancePoint] = []  // 比较基准 (沪深300+标普500加权)
    /// 全资产股息聚合 (今年预计股息税后折人民币 + 覆盖率).
    public var dividendSummary: DividendSummary?

    // 特别关注（第 4 个模块）
    /// 合成日 K（20 根）+ 当日涨幅/跌幅最高两只；口径见 FocusComposer / ADR 0008。
    public var focusBoard: FocusBoard = .empty
    /// 「特别关注」点小图 → 跳到「资产明细」并选中该标的的单向通道。
    /// 资产明细消费后立刻清空（它不是持久状态，只是跨模块的一次性意图）。
    public var pendingAssetSelection: String?

    // 模块2
    public var perspectives: [AssetPerspectiveRow] = []
    /// 编辑草稿的唯一改动来源是 Core 的 `HoldingEditor`（有单测）；这里只暴露只读快照。
    private var editor = HoldingEditor()
    public var holdingDrafts: [String: HoldingDraft] { editor.drafts }

    // 能力4 财务分析 (重做: 逐季度底稿, 9 手动字段 + 派生行)
    /// 底稿本体与落盘节奏在 Core 的 `QuarterlyDesk`（有单测）；这里只暴露只读快照。
    private let desk: QuarterlyDesk
    public var quarterlyReports: [QuarterlyReport] { desk.reports }

    /// 外部世界的唯一入口（路径 / UserDefaults / 时钟）。注入后 AppModel 才能在测试里跑。
    @ObservationIgnored private let env: AppEnvironment

    // 能力2 汇率 (币种→人民币), 自动抓取 + 手动可覆盖
    public var fxRates: [FxRate] = []

    // 能力2 optimizer state
    public var targetReturn: Double = 0.10
    public var isOptimizing = false
    public var optimizeSteps: [OptimizationStep] = []
    public var lastOptimization: OptimizationResult?
    public var optimizeError: String?

    // 能力2 新标的测试 (临时标的重跑优化; 结果独立于 lastOptimization, 不触碰资产总览数据)
    public var isTestOptimizing = false
    public var testOptimization: OptimizationResult?
    public var testOptimizeError: String?
    public var testSteps: [OptimizationStep] = []
    public var testTickersUsed: [String] = []

    public var statusMessage: String?
    public var lastUpdated: String?

    /// 「一次优化运行」的编排（Core 的 module）。主运行与新标的测试共用同一个实例，
    /// 因此二者天然互斥（`isBusy`）。
    @ObservationIgnored private let runner: OptimizationRunner
    @ObservationIgnored private var backupManager: BackupManager?
    @ObservationIgnored private var backupTask: Task<Void, Never>?
    public var lastBackupAt: String?

    // 隐私: 隐藏数字 (默认关闭 = 显示; 每次启动恢复为显示, 不持久化).
    public var hideNumbers = false

    // 备份导入确认面板的状态. 「导入备份」是全局工具条按钮 (三大模块共用),
    // 面板本身由 ContentView 统一挂载一次, 因此状态放在 store 里而不是某个模块的 @State.
    public var pendingImportURL: URL?
    public var clearAssetsOnImport = false
    public var clearFinancialsOnImport = false

    public init(db: Database, optimizer: OptimizationService,
                env: AppEnvironment = .current) {
        self.db = db
        self.repository = Repository(db: db)
        self.optimizer = optimizer
        self.runner = OptimizationRunner(driver: optimizer)
        self.env = env
        self.desk = QuarterlyDesk(persistence: QuarterlyDesk.Persistence(
            upsert: { try db.upsertQuarterlyReports($0) },
            remove: { try db.deleteQuarterlyReport(periodEnd: $0) },
            rename: { try db.renameQuarterlyReport(from: $0, to: $1) }))
        desk.onPersistFailure = { [weak self] message in
            self?.statusMessage = "保存失败: " + message
        }
    }

    /// Bootstrap the store using dev/AppPaths defaults.
    public static func makeDefault() -> AppStore? {
        let env = AppEnvironment.current
        do {
            let db = try Database(path: env.databaseURL.path)
            let optimizer = OptimizationService(db: db, sidecar: env.makeSidecar(), logsDir: env.logsDir)
            let store = AppStore(db: db, optimizer: optimizer, env: env)
            // 目标收益率的持久化也走环境（视图不再直接碰 UserDefaults）。
            if let saved = env.defaults.object(forKey: AppEnvironment.targetReturnKey) as? Double {
                store.targetReturn = saved
            }
            store.startBackupScheduler()
            return store
        } catch {
            return nil
        }
    }

    /// 设置优化目标收益率并持久化（视图只调这个方法）。
    public func setTargetReturn(_ value: Double) {
        targetReturn = value
        env.defaults.set(value, forKey: AppEnvironment.targetReturnKey)
    }

    // MARK: 能力3 — daily auto-backup scheduler

    /// Kick off a loop that checks hourly and creates one backup per day.
    public func startBackupScheduler() {
        let bm = BackupManager(db: db, backupDir: env.backupsURL)
        backupManager = bm
        backupTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                do {
                    if let dest = try self.backupManager?.ensureDailyBackup() {
                        self.lastBackupAt = dest.lastPathComponent
                    }
                } catch { /* non-fatal: skip this tick */ }
                try? await Task.sleep(for: .seconds(3600))
            }
        }
    }

    // MARK: loading

    public func loadAll() {
        do {
            // 一次装配：allocation / perspectives / 股息 / 绩效 / 特别关注 共用同一个估值。
            let valuation = try repository.loadValuation()
            allocation = valuation.allocationSnapshot
            valuationWarnings = valuation.warnings
            let perf = try repository.fetchPerformance(valuation: valuation)
            performancePoints = perf.points
            performanceSummary = perf.summary
            focusBoard = try repository.fetchFocusBoard(valuation: valuation)
            dividendSummary = try repository.fetchDividendSummary(valuation: valuation)
            perspectives = try repository.fetchAssetPerspectives(valuation: valuation)
            editor.seed(perspectives)
            fxRates = try db.fetchFxRates()
            desk.replaceAll(try repository.fetchQuarterlyReports())
            lastUpdated = DateFormatters.nowISO()
            statusMessage = nil
        } catch {
            valuationWarnings = []
            statusMessage = "加载失败: \(error)"
        }
    }

    /// 启动时自动抓取有效汇率 + 行情 (能力1/能力2), 异步不阻塞 UI.
    /// If the bundle contains a purge_request.txt resource and no .purge_done
    /// marker exists in Application Support, purge all seeded data (one-time).
    public func startupRefresh() async {
        // One-time purge: bundle has purge_request.txt → purge all assets.
        // Guard with .purge_done in Application Support so it only fires once.
        if AppPaths.isBundled,
           Bundle.main.url(forResource: "purge_request", withExtension: "txt") != nil {
            let doneFlag = AppPaths.supportDir().appendingPathComponent(".purge_done")
            if !FileManager.default.fileExists(atPath: doneFlag.path) {
                purgeAllData()
                try? "done".write(to: doneFlag, atomically: true, encoding: .utf8)
            }
        }
        loadAll()
        await refreshFxRates()
        await refreshPrices()
        await fetchBenchmark()
    }

    /// Delete all assets (cascade holdings/prices/quotes) and snapshots.
    /// Used for one-time purge of seeded (imported) data.
    public func purgeAllData() {
        do {
            try db.exec("DELETE FROM quotes")
            try db.exec("DELETE FROM prices")
            try db.exec("DELETE FROM dividends")
            try db.exec("DELETE FROM dividend_fetch_meta")
            try db.exec("DELETE FROM holdings")
            try db.exec("DELETE FROM assets")
            try db.exec("DELETE FROM snapshots")
            statusMessage = "已清空全部标的数据"
        } catch {
            statusMessage = "清空失败: \(error)"
        }
    }

    // MARK: 模块2 / 能力4 — editing & save

    /// 脏判定在 Core 的 `HoldingEditor`（有单测）。
    public var hasUnsavedChanges: Bool { editor.isDirty(against: perspectives) }

    /// Persist all holding drafts back to the holdings table and reload.
    public func savePerspectives() {
        do {
            for (key, draft) in editor.drafts {
                try db.updateHolding(assetKey: key, quantity: draft.quantity,
                                     costBasis: draft.costBasis, currency: draft.currency)
            }
            loadAll()
            statusMessage = "已保存"
        } catch {
            statusMessage = "保存失败: \(error)"
        }
    }

    /// A two-way Binding into a holding draft's Double field.
    public func holdingBinding(_ key: String,
                               _ keyPath: WritableKeyPath<HoldingDraft, Double>) -> Binding<Double> {
        Binding(
            get: { self.editor.draft(key)[keyPath: keyPath] },
            set: { value in
                if keyPath == \HoldingDraft.quantity {
                    self.editor.setQuantity(key, value)
                } else {
                    self.editor.setCostBasis(key, value)
                }
            }
        )
    }

    // MARK: 能力4 — 逐季度财务分析底稿

    /// 网格内联编辑: 更新某季度某手动字段 (内存立即生效, 派生行实时重算; 落盘防抖).
    /// 防抖与「删除必须取消未落盘快照」的时序规则在 Core 的 `QuarterlyDesk`（有单测）。
    public func updateQuarterlyField(periodEnd: String, field: QuarterlyField, value: Double?) {
        desk.update(periodEnd: periodEnd, field: field, value: value)
    }

    /// 添加一个季度列 (默认 = 最后一列之后的下一个季末; 首列 = 当前季末).
    public func addQuarter() {
        switch desk.add() {
        case .added(let end):
            statusMessage = "已添加季度 " + QuarterlyMetrics.quarterLabel(end)
        case .duplicate(let end):
            statusMessage = "季度 \(end) 已存在"
        case .failed(let message):
            statusMessage = "保存失败: " + message
        default:
            break
        }
    }

    public func deleteQuarter(periodEnd: String) {
        switch desk.delete(periodEnd: periodEnd) {
        case .deleted:
            statusMessage = "已删除季度"
        case .failed(let message):
            statusMessage = "删除失败: " + message
        default:
            break
        }
    }

    // MARK: 能力4 — 「总市值 / 总成本」一键填入当前值 (Q6=C / Q7=A / Q8=A / Q10=B / Q15=B)

    /// 财务分析里「总市值」「总成本」两格可一键填入的**当前**值。
    /// 规则本体在 `QuarterlyFill`（PortfolioCore，有单测锁口径），这里只负责把状态喂进去。
    /// ⚠️ 填的是当下实时值，不是该季末的历史值 —— 见 `docs/adr/0001`。
    public func quarterlyFillValue(periodEnd: String, field: QuarterlyField) -> Double? {
        QuarterlyFill.value(field: field, periodEnd: periodEnd,
                            reports: quarterlyReports, allocation: allocation)
    }

    /// 执行填入：走与手输完全相同的路径（`updateQuarterlyField` → 同样防抖落盘），
    /// 因此派生行会立刻跟着重算。返回填进去的数值，供视图同步单元格草稿。
    @discardableResult
    public func applyQuarterlyFill(periodEnd: String, field: QuarterlyField) -> Double? {
        guard let v = quarterlyFillValue(periodEnd: periodEnd, field: field) else {
            statusMessage = "暂无可填入的当前值（行情或成本为空）"
            return nil
        }
        updateQuarterlyField(periodEnd: periodEnd, field: field, value: v)
        statusMessage = "已填入当前\(field.label)：¥" + String(format: "%.2f", v)
        return v
    }

    /// 修改季度列的截止日 (表头编辑).
    public func renameQuarter(from oldEnd: String, to newEnd: String) {
        switch desk.rename(from: oldEnd, to: newEnd) {
        case .renamed(let to):
            statusMessage = "已改为 " + to
        case .duplicate(let end):
            statusMessage = "季度 \(end) 已存在"
        case .failed(let message):
            statusMessage = "修改失败: " + message
        case .unchanged:
            break
        default:
            break
        }
    }

    // MARK: 能力2 — 标的增删 + 联网校验 + 汇率

    /// Currencies offered in the pickers.
    public static let currencyOptions = ["CNY", "USD", "HKD", "JPY", "SGD", "EUR", "GBP", "AUD", "CAD", "CHF"]

    private static func todayString() -> String {
        DateFormatters.todayUTC()
    }

    /// Validate a ticker online; 6位基金代码走天天基金, 其余走 Yahoo Finance。
    public func lookupSymbol(_ symbol: String) async -> (valid: Bool, currency: String, name: String?, message: String) {
        if let code = AssetTicker.fundCode(symbol) {
            let src = EastmoneySource()
            do {
                let info = try await src.lookup(symbol: code)
                let nameText = info.name ?? ""
                return (true, "CNY", info.name,
                        "校验通过（天天基金）" + (nameText.isEmpty ? "" : "：" + nameText))
            } catch {
                return (false, "CNY", nil, "天天基金校验失败：\(error)")
            }
        }
        let src = YahooFinanceSource()
        do {
            let info = try await src.lookup(symbol: symbol)
            let nameText = info.name ?? ""
            return (true, info.currency, info.name,
                    "校验通过" + (nameText.isEmpty ? "" : "：" + nameText))
        } catch {
            return (false, "USD", nil, "校验失败：\(error)")
        }
    }

    /// Add a new asset target (with a zero holding so it shows up in 资产明细).
    /// 池不再由调用方传入：它由市场派生（Q27 删掉了手选池的入口）。
    public func addAsset(key: String, name: String, ticker: String?, market: AssetMarket?,
                         assetClass: String, currency: String) {
        do {
            // New targets get sort_order = max+1 → appended at the end of the list.
            let nextOrder = ((try? db.fetchAssets())?.compactMap { $0.sortOrder }.max() ?? 0) + 1
            let identity = AssetIdentity(key: key, ticker: ticker,
                                         marketRaw: market?.rawValue, currency: currency)
            let asset = Asset(key: key, name: name, ticker: ticker, market: identity.market,
                              assetClass: assetClass, pool: identity.pool,
                              currency: currency, source: "manual",
                              sortOrder: nextOrder)
            try db.insertAsset(asset)
            try db.upsertHoldings([Holding(assetKey: key, quantity: 0, costBasis: 0,
                                           currency: currency,
                                           asOfDate: Self.todayString())])
            loadAll()
            statusMessage = "已添加 \(name)"
        } catch {
            statusMessage = "添加失败: \(error)"
        }
    }

    /// Persist drag-to-reorder of the 资产明细 list (module 2).
    /// Rewrites every asset's sort_order to 0..n-1 following the new arrangement.
    public func moveAsset(from source: IndexSet, to destination: Int) {
        var rows = perspectives
        rows.move(fromOffsets: source, toOffset: destination)
        do {
            try db.updateAssetSortOrders(rows.enumerated().map { ($0.element.assetKey, Double($0.offset)) })
            let drafts = editor.drafts
            loadAll()
            editor.restore(drafts)  // 拖动排序不应清掉「编辑持仓」里未保存的草稿
        } catch {
            statusMessage = "保存排序失败: \(error)"
        }
    }

    /// Delete an asset target (and its holding / prices / financials).
    public func deleteAsset(key: String) {
        do {
            try db.deleteAsset(key: key)
            loadAll()
            statusMessage = "已删除标的"
        } catch {
            statusMessage = "删除失败: \(error)"
        }
    }

    /// Two-way Binding into a holding draft's currency (String).
    public func holdingCurrencyBinding(_ key: String) -> Binding<String> {
        Binding(
            get: { self.editor.draft(key).currency },
            set: { self.editor.setCurrency(key, $0) }
        )
    }

    /// Auto-fetch CNY rates for every non-CNY currency in the portfolio (能力1).
    /// 取数/落库/失败分组在 `FxRateSync`（Core）；这里只决定币种清单。
    public func refreshFxRates() async {
        statusMessage = "正在更新汇率…"
        var currencies = Set(perspectives.map { $0.currency }).filter { $0 != "CNY" }
        if currencies.isEmpty { currencies = ["USD", "HKD", "JPY", "SGD"] }
        let src = YahooFinanceSource()
        let report = await FxRateSync.run(currencies: Array(currencies), now: Date(), into: db) { ccy in
            try await src.lookup(symbol: ccy + "CNY=X").price
        }
        loadAll()
        statusMessage = "汇率更新完成" + (report.failed.isEmpty ? "" : "，失败 \(report.failed.count) 个币种")
    }

    /// Manual override of a single FX rate.
    public func setFxRate(currency: String, rateToCny: Double) {
        do {
            try db.upsertFxRates([FxRate(currency: currency, rateToCny: rateToCny,
                                         asOfDate: Self.todayString(), source: "manual")])
            loadAll()
            statusMessage = "已保存汇率"
        } catch {
            statusMessage = "保存汇率失败: \(error)"
        }
    }

    // MARK: 能力1 — data refresh (auto-fetch public quotes)

    /// Resolve an asset to (data source, symbol). 唯一解析入口 = AssetIdentity（ADR 0010）：
    /// 基金代码（大小写不敏感）→ 天天基金；目录 key → 目录里的源；其余 → Yahoo。
    private func resolveDataSource(asset: Asset) -> (source: any DataSource, symbol: String)? {
        guard let feed = AssetIdentity(asset).feed else { return nil }
        switch feed.kind {
        case .fund: return (EastmoneySource(), feed.symbol)
        case .yahoo: return (YahooFinanceSource(), feed.symbol)
        }
    }

    /// 刷新行情 + 股息 (能力1)。
    ///
    /// 编排只剩「组计划 → 跑同步 → 更新文案 → 重载 → 拉宏观利率」；并发限流、股息缓存、
    /// 批量落库、失败聚合全在 `MarketDataSync`（Core，有单测）。
    /// 失败计数与 statusMessage 语义保持与改造前一致。
    public func refreshPrices() async {
        statusMessage = "正在更新行情…"
        let assets = (try? db.fetchAssets()) ?? []
        let assetByKey = Dictionary(uniqueKeysWithValues: assets.map { ($0.key, $0) })
        let metas = (try? db.fetchDividendFetchMetas()) ?? [:]
        let now = Date()
        let years = MarketDataSync.dividendYears(now: now)

        let targets: [SyncPlan.Target] = perspectives.compactMap { row in
            guard let a = assetByKey[row.assetKey],
                  let (source, symbol) = resolveDataSource(asset: a) else { return nil }
            let skip = metas[row.assetKey].map {
                MarketDataSync.cacheIsFresh($0, now: now)
            } ?? false
            return SyncPlan.Target(assetKey: row.assetKey, source: source,
                                   symbol: symbol, skipDividends: skip)
        }

        let report = await MarketDataSync.run(
            SyncPlan(targets: targets, dividendYears: years, now: now), into: db)
        statusMessage = "行情更新完成：\(report.pricePointCount) 个数据点"
            + (report.failedAssetKeys.isEmpty ? "" : "，失败 \(report.failedAssetKeys.count) 个")
        loadAll()
        await refreshMacroRates()
    }

    // MARK: 能力2 — macro rates (动态 RF)

    /// 拉 CN/US 10 年期国债收益率（akshare 子进程）并缓存进 `macro_rates`。
    /// 本体在 `MacroRateSync`（Core）：解析是纯函数、子进程在后台线程跑 —— 不再卡主线程。
    public func refreshMacroRates() async {
        await MacroRateSync.refresh(config: env.sidecarConfig, into: db)
    }

    // MARK: 能力1 — 比较基准 (沪深300 + 标普500 加权, 与优化器基准一致)

    /// 境内/境外池权重 — 来自组合估值的投影（池由每个标的的**市场**派生）。
    /// 池只有境内/境外两种，因此两者必然归一；无持仓时回退 50/50。
    public var poolShares: (domestic: Double, overseas: Double) {
        allocation?.poolShares ?? (0.5, 0.5)
    }

    /// Fetch and store benchmark history (SPY + 000300.SS weighted by the live
    /// domestic/overseas pool split from 资产总览). Falls back to 50/50 when no data.
    /// 合成规则（并集/前向填充/归一/裁剪）在 `BenchmarkSeries`（Core，纯函数）。
    public func fetchBenchmark() async {
        let weights = poolShares
        let src = YahooFinanceSource()
        var spyPoints: [PricePoint] = []
        var csiPoints: [PricePoint] = []
        do { spyPoints = try await src.fetchHistory(symbol: "SPY") } catch {}
        do { csiPoints = try await src.fetchHistory(symbol: "000300.SS") } catch {}
        // Try alternative CSI300 symbol if 000300.SS fails
        if csiPoints.isEmpty { do { csiPoints = try await src.fetchHistory(symbol: "510300.SS") } catch {} }
        guard !spyPoints.isEmpty || !csiPoints.isEmpty else { return }
        benchmarkPoints = BenchmarkSeries.compose(spy: spyPoints, csi: csiPoints,
                                                  domesticWeight: weights.domestic,
                                                  overseasWeight: weights.overseas)
    }

    // MARK: 能力2 — optimizer

    /// 组装 live 提取文件：把「资产总览 / 资产明细」的实时统计写进提取文件的一份副本。
    ///
    /// 覆盖规则（池权重 / us_equity / domestic_holdings / held_assets / RF）的**本体在
    /// `LiveExtract`（PortfolioCore，有单测）**；这里只负责取数与落盘。
    /// 优化运行与敏感性分析读的是**同一个**文件（`env.liveExtractURL`）。
    private func liveExtractURL() -> URL? {
        let src = env.extractURL
        guard let data = try? Data(contentsOf: src),
              let base = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return nil }

        let assets = (try? db.fetchAssets()) ?? []
        let byKey = Dictionary(uniqueKeysWithValues: assets.map { ($0.key, $0) })
        let rows = perspectives.map { row -> LiveExtract.Row in
            let a = byKey[row.assetKey]
            return LiveExtract.Row(assetKey: row.assetKey,
                                   ticker: a?.ticker,
                                   name: row.name,
                                   assetMarket: a?.market?.rawValue,
                                   rowMarket: row.market?.rawValue,
                                   assetClass: row.assetClass,
                                   storedAssetClass: a?.assetClass,
                                   pool: row.pool.rawValue,
                                   valueCny: row.valueCny)
        }
        let alloc = allocation.map { a in
            LiveExtract.Allocation(totalValue: a.totalValue,
                                   domesticValue: a.domesticValue,
                                   overseasValue: a.overseasValue,
                                   domesticWeight: a.poolShares.domestic,
                                   overseasWeight: a.poolShares.overseas)
        }
        let input = LiveExtract.Input(allocation: alloc, rows: rows,
                                      cn10y: try? db.fetchMacroRate("cn_10y")?.value,
                                      us10y: try? db.fetchMacroRate("us_10y")?.value)
        let obj = LiveExtract.apply(to: base, input)
        let dst = env.liveExtractURL
        guard let out = try? JSONSerialization.data(withJSONObject: obj) else { return nil }
        do { try out.write(to: dst) } catch { return nil }
        return dst
    }

    /// 提取文件缺失时的提示（路径取本次解析到的那一个）。
    private static func missingExtractMessage(_ url: URL) -> String {
        "未找到优化器输入文件 extract_app.json：\n\(url.path)\n\n"
            + "请先生成提取文件（终端执行 pm-cli extract <表格文件路径>），"
            + "再把生成的 extract_app.json 放到上面这个目录后重试。"
    }

    public func runOptimization() {
        guard !isOptimizing, !isTestOptimizing else { return }
        let extract = liveExtractURL() ?? env.extractURL
        guard FileManager.default.fileExists(atPath: extract.path) else {
            optimizeError = Self.missingExtractMessage(extract)
            return
        }
        isOptimizing = true
        optimizeSteps = []
        optimizeError = nil
        lastOptimization = nil

        let request = OptimizationRequest(extract: extract, targetReturn: targetReturn)
        Task { [weak self] in
            guard let self else { return }
            await self.runner.run(request) { event in
                self.consume(event, isTest: false)
            }
        }
    }

    // MARK: 能力2 — 新标的测试

    /// 临时加入标的重跑一次优化。结果写入 testOptimization（「测试结果」弹窗），
    /// 不影响 lastOptimization 与资产总览数据; 仅 optimization_runs 留痕
    /// (paramsDesc 带 test=...)。与普通优化互斥（共用同一个 runner）。
    /// testClass: 标的类别 broad / strategy / active（见 asset_classes.py 的口径说明），
    /// 决定 μ 的构造方式与历史窗口（μ 套 −10%~30%、σ 套 1%~60% 统一护栏）；缺省 active。
    public func runTestOptimization(tickers: [String], testClass: String = "active") {
        guard !isOptimizing, !isTestOptimizing, !tickers.isEmpty else { return }
        let extract = liveExtractURL() ?? env.extractURL
        guard FileManager.default.fileExists(atPath: extract.path) else {
            testOptimizeError = Self.missingExtractMessage(extract)
            return
        }
        isTestOptimizing = true
        testSteps = []
        testOptimizeError = nil
        testOptimization = nil
        testTickersUsed = tickers

        let request = OptimizationRequest(extract: extract, targetReturn: targetReturn,
                                          testTickers: tickers, testClass: testClass)
        Task { [weak self] in
            guard let self else { return }
            await self.runner.run(request) { event in
                self.consume(event, isTest: true)
            }
        }
    }

    /// 把一次运行的事件写回 store 状态 —— 主运行与新标的测试只差「写哪一组字段」。
    private func consume(_ event: OptimizationEvent, isTest: Bool) {
        switch event {
        case .started:
            break
        case .steps(let fresh):
            if isTest {
                testSteps.append(contentsOf: fresh)
            } else {
                optimizeSteps.append(contentsOf: fresh)
            }
        case .finished(let out):
            if isTest {
                testOptimization = out.result
                testOptimizeError = out.error
                if !out.steps.isEmpty { testSteps = out.steps }
                isTestOptimizing = false
            } else {
                lastOptimization = out.result
                optimizeError = out.error
                if !out.steps.isEmpty { optimizeSteps = out.steps }
                isOptimizing = false
                loadAll()
                Task { await fetchBenchmark() }  // 用新权重刷新基准线
            }
        case .failed(let message):
            if isTest {
                testOptimizeError = message
                isTestOptimizing = false
            } else {
                optimizeError = message
                isOptimizing = false
            }
        }
    }

    // MARK: 能力2 — 敏感性分析

    /// 敏感性分析：与结果页同源（读同一份 live 提取文件），在**后台线程**执行。
    /// 一次扫描约 1–3 分钟；调用方必须 await（过去在 View 里同步跑 sidecar → 主线程卡死）。
    public func runSensitivity(for result: OptimizationResult) async throws -> SensitivityAnalysisResult {
        let extract = env.liveExtractURL
        guard FileManager.default.fileExists(atPath: extract.path) else {
            throw SensitivityRunError.missingExtract(extract.path)
        }
        return try await OptimizationService.runSensitivityOffMain(
            config: env.sidecarConfig,
            extractJSON: extract,
            anchor: SensitivityAnchor(targetReturn: result.targetReturn,
                                      sleeve: result.sleeveParams,
                                      actualReturn: result.portfolio.expectedReturn,
                                      volatility: result.portfolio.volatility))
    }

    // MARK: 能力3 — 备份数据导入/导出 (可移植 JSON)

    public func exportBackup(to url: URL) throws {
        // 财务分析字段是防抖落盘 (0.6s), 导出前先 flush, 避免刚录入的数据丢在内存里.
        desk.flush()
        let bm = BackupManager(db: db, backupDir: env.backupsURL)
        try bm.exportJSON(to: url)
    }

    public func importBackup(from url: URL, clearAssets: Bool, clearFinancials: Bool) async throws {
        let bm = BackupManager(db: db, backupDir: env.backupsURL)
        try bm.importJSON(from: url, clearAssets: clearAssets, clearFinancials: clearFinancials)
        loadAll()
        await startupRefresh()
    }

    /// 用户在导入确认面板点「导入」后执行; 清空与否取面板里勾选的状态.
    public func performImportBackup() {
        guard let url = pendingImportURL else { return }
        let clearAssets = clearAssetsOnImport
        let clearFinancials = clearFinancialsOnImport
        pendingImportURL = nil
        Task {
            do {
                try await importBackup(from: url, clearAssets: clearAssets, clearFinancials: clearFinancials)
                statusMessage = "已导入备份: \(url.lastPathComponent)"
            } catch {
                statusMessage = "导入备份失败: \(error)"
            }
        }
    }
}

/// 敏感性分析的前置条件错误（视图直接展示 localizedDescription）。
public enum SensitivityRunError: LocalizedError {
    case missingExtract(String)

    public var errorDescription: String? {
        switch self {
        case .missingExtract(let path):
            return "未找到提取文件：\(path)。请先运行优化。"
        }
    }
}