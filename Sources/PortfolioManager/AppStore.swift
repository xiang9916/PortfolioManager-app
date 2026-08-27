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

    /// Extracted .numbers state (input to the optimizer).
    public static func extractJSONURL() -> URL {
        if isBundled { return supportDir().appendingPathComponent("extract_app.json") }
        return URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
            .appendingPathComponent("tmp/extract_app.json")
    }

    /// Daily-backup directory (sibling of the active DB's parent).
    public static func backupsURL() -> URL {
        let parent = databaseURL().deletingLastPathComponent()
        let dir = parent.appendingPathComponent("backups", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }
}

/// Editable draft of a holding's fields (资产透视 editing).
public struct HoldingDraft: Hashable {
    public var quantity: Double
    public var costBasis: Double
    public var currency: String
    public init(quantity: Double = 0, costBasis: Double = 0, currency: String = "CNY") {
        self.quantity = quantity
        self.costBasis = costBasis
        self.currency = currency
    }
}

/// Central @MainActor store: owns the DB + repository + optimizer and exposes
/// observable state to all SwiftUI views.
@MainActor
@Observable
public final class AppStore {
    public let db: Database
    public let repository: Repository
    public let optimizer: OptimizationService

    // 模块1 / 能力1
    public var allocation: AllocationSnapshot?
    public var performancePoints: [PerformancePoint] = []
    public var performanceSummary: PerformanceSummary?
    public var benchmarkPoints: [PerformancePoint] = []  // 比较基准 (沪深300+标普500加权)

    // 模块2
    public var perspectives: [AssetPerspectiveRow] = []
    /// Editing drafts (assetKey -> draft). Populated on load; saved via savePerspectives().
    public var holdingDrafts: [String: HoldingDraft] = [:]

    // 能力4 财务分析 (重做: 逐季度底稿, 9 手动字段 + 派生行)
    public var quarterlyReports: [QuarterlyReport] = []
    @ObservationIgnored private var quarterlyPersistTask: Task<Void, Never>?

    // 能力2 汇率 (币种→人民币), 自动抓取 + 手动可覆盖
    public var fxRates: [FxRate] = []

    // 能力2 optimizer state
    public var targetReturn: Double = 0.10
    public var isOptimizing = false
    public var optimizeSteps: [OptimizationStep] = []
    public var lastOptimization: OptimizationResult?
    public var optimizeError: String?

    public var statusMessage: String?
    public var lastUpdated: String?

    @ObservationIgnored private var pollTask: Task<Void, Never>?
    @ObservationIgnored private var backupManager: BackupManager?
    @ObservationIgnored private var backupTask: Task<Void, Never>?
    public var lastBackupAt: String?

    public init(db: Database, optimizer: OptimizationService) {
        self.db = db
        self.repository = Repository(db: db)
        self.optimizer = optimizer
    }

    /// Bootstrap the store using dev/AppPaths defaults.
    public static func makeDefault() -> AppStore? {
        do {
            let dbURL = AppPaths.databaseURL()
            let db = try Database(path: dbURL.path)
            let sidecar = PythonSidecar(
                interpreterPath: AppPaths.interpreterPath(),
                scriptsDir: AppPaths.scriptsURL(),
                currentDirectoryURL: URL(fileURLWithPath: FileManager.default.currentDirectoryPath))
            let logsDir = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
                .appendingPathComponent("tmp/optimizer_logs", isDirectory: true)
            let optimizer = OptimizationService(db: db, sidecar: sidecar, logsDir: logsDir)
            let store = AppStore(db: db, optimizer: optimizer)
            // Restore persisted optimizer target return (if saved previously)
            if let saved = UserDefaults.standard.object(forKey: "optimizer.targetReturn") as? Double {
                store.targetReturn = saved
            }
            store.startBackupScheduler()
            return store
        } catch {
            return nil
        }
    }

    // MARK: 能力3 — daily auto-backup scheduler

    /// Kick off a loop that checks hourly and creates one backup per day.
    public func startBackupScheduler() {
        let bm = BackupManager(db: db, backupDir: AppPaths.backupsURL())
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
            allocation = try repository.fetchAllocation()
            let perf = try repository.fetchPerformance()
            performancePoints = perf.points
            performanceSummary = perf.summary
            perspectives = try repository.fetchAssetPerspectives()
            holdingDrafts = Dictionary(uniqueKeysWithValues: perspectives.map {
                ($0.assetKey, HoldingDraft(quantity: $0.quantity, costBasis: $0.costBasis,
                                           currency: $0.currency))
            })
            fxRates = try db.fetchFxRates()
            quarterlyReports = try repository.fetchQuarterlyReports()
            lastUpdated = DateFormatters.nowISO()
            statusMessage = nil
        } catch {
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
    /// Used for one-time purge of seeded .numbers data.
    public func purgeAllData() {
        do {
            try db.exec("DELETE FROM quotes")
            try db.exec("DELETE FROM prices")
            try db.exec("DELETE FROM holdings")
            try db.exec("DELETE FROM assets")
            try db.exec("DELETE FROM snapshots")
            statusMessage = "已清空全部标的数据"
        } catch {
            statusMessage = "清空失败: \(error)"
        }
    }

    // MARK: 模块2 / 能力4 — editing & save

    public var hasUnsavedChanges: Bool {
        for (key, draft) in holdingDrafts {
            guard let row = perspectives.first(where: { $0.assetKey == key }) else { continue }
            if draft.quantity != row.quantity || draft.costBasis != row.costBasis || draft.currency != row.currency {
                return true
            }
        }
        return false
    }

    /// Persist all holding drafts back to the holdings table and reload.
    public func savePerspectives() {
        do {
            for (key, draft) in holdingDrafts {
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
    public func holdingBinding(_ key: String, _ keyPath: WritableKeyPath<HoldingDraft, Double>) -> Binding<Double> {
        Binding(
            get: { self.holdingDrafts[key]?[keyPath: keyPath] ?? 0 },
            set: { v in
                var d = self.holdingDrafts[key] ?? HoldingDraft()
                d[keyPath: keyPath] = v
                var copy = self.holdingDrafts
                copy[key] = d
                self.holdingDrafts = copy
            }
        )
    }

    // MARK: 能力4 — 逐季度财务分析底稿

    /// 网格内联编辑: 更新某季度某手动字段 (内存立即生效, 派生行实时重算; 落盘防抖).
    public func updateQuarterlyField(periodEnd: String, field: QuarterlyField, value: Double?) {
        guard let i = quarterlyReports.firstIndex(where: { $0.periodEnd == periodEnd }) else { return }
        quarterlyReports[i][keyPath: field.keyPath] = value
        scheduleQuarterlyPersist()
    }

    /// 添加一个季度列 (默认 = 最后一列之后的下一个季末; 首列 = 当前季末).
    public func addQuarter() {
        let next: String
        if let last = quarterlyReports.map(\.periodEnd).max(),
           let n = QuarterlyMetrics.nextQuarterEnd(after: last) {
            next = n
        } else {
            next = QuarterlyMetrics.currentQuarterEnd()
        }
        guard !quarterlyReports.contains(where: { $0.periodEnd == next }) else {
            statusMessage = "季度 \(next) 已存在"
            return
        }
        quarterlyReports.append(QuarterlyReport(periodEnd: next, source: "manual"))
        quarterlyReports.sort { $0.periodEnd < $1.periodEnd }
        flushQuarterlyPersist()
        statusMessage = "已添加季度 " + QuarterlyMetrics.quarterLabel(next)
    }

    public func deleteQuarter(periodEnd: String) {
        quarterlyPersistTask?.cancel()   // 取消未落盘快照, 防止删行被旧快照复活
        quarterlyReports.removeAll { $0.periodEnd == periodEnd }
        do {
            try db.deleteQuarterlyReport(periodEnd: periodEnd)
            statusMessage = "已删除季度"
        } catch {
            statusMessage = "删除失败: \(error)"
        }
    }

    /// 修改季度列的截止日 (表头编辑).
    public func renameQuarter(from oldEnd: String, to newEnd: String) {
        let trimmed = newEnd.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty, trimmed != oldEnd else { return }
        guard !quarterlyReports.contains(where: { $0.periodEnd == trimmed }) else {
            statusMessage = "季度 \(trimmed) 已存在"
            return
        }
        do {
            try db.renameQuarterlyReport(from: oldEnd, to: trimmed)
            quarterlyPersistTask?.cancel()
            if let i = quarterlyReports.firstIndex(where: { $0.periodEnd == oldEnd }) {
                quarterlyReports[i].periodEnd = trimmed
                quarterlyReports.sort { $0.periodEnd < $1.periodEnd }
            }
            flushQuarterlyPersist()
            statusMessage = "已改为 " + trimmed
        } catch {
            statusMessage = "修改失败: \(error)"
        }
    }

    /// 防抖落盘: 停止输入 0.6s 后整表 upsert (表小, 整表写无压力).
    private func scheduleQuarterlyPersist() {
        quarterlyPersistTask?.cancel()
        let snapshot = quarterlyReports
        quarterlyPersistTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(0.6))
            guard !Task.isCancelled else { return }
            do { try self?.db.upsertQuarterlyReports(snapshot) }
            catch { self?.statusMessage = "保存失败: \(error)" }
        }
    }

    /// 立即落盘 (添加/删除/改名等结构性操作后调用).
    private func flushQuarterlyPersist() {
        quarterlyPersistTask?.cancel()
        quarterlyPersistTask = nil
        do { try db.upsertQuarterlyReports(quarterlyReports) }
        catch { statusMessage = "保存失败: \(error)" }
    }

    // MARK: 能力2 — 标的增删 + 联网校验 + 汇率

    /// Currencies offered in the pickers.
    public static let currencyOptions = ["CNY", "USD", "HKD", "JPY", "SGD", "EUR", "GBP", "AUD", "CAD", "CHF"]

    private static func todayString() -> String {
        DateFormatters.todayUTC()
    }

    /// Validate a ticker online; 6位基金代码走天天基金, 其余走 Yahoo Finance。
    public func lookupSymbol(_ symbol: String) async -> (valid: Bool, currency: String, name: String?, message: String) {
        if let code = Self.fundCode(symbol) {
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

    /// Add a new asset target (with a zero holding so it shows up in 资产透视).
    public func addAsset(key: String, name: String, ticker: String?, market: String?,
                         assetClass: String, pool: Pool, currency: String) {
        do {
            // New targets get sort_order = max+1 → appended at the end of the list.
            let nextOrder = ((try? db.fetchAssets())?.compactMap { $0.sortOrder }.max() ?? 0) + 1
            let asset = Asset(key: key, name: name, ticker: ticker, market: market,
                              assetClass: assetClass, pool: pool, currency: currency, source: "manual",
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

    /// Persist drag-to-reorder of the 资产透视 list (module 2).
    /// Rewrites every asset's sort_order to 0..n-1 following the new arrangement.
    public func moveAsset(from source: IndexSet, to destination: Int) {
        var rows = perspectives
        rows.move(fromOffsets: source, toOffset: destination)
        do {
            try db.updateAssetSortOrders(rows.enumerated().map { ($0.element.assetKey, Double($0.offset)) })
            let drafts = holdingDrafts
            loadAll()
            holdingDrafts = drafts  // 拖动排序不应清掉「编辑持仓」里未保存的草稿
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
            get: { self.holdingDrafts[key]?.currency ?? "CNY" },
            set: { v in
                var d = self.holdingDrafts[key] ?? HoldingDraft()
                d.currency = v
                var copy = self.holdingDrafts
                copy[key] = d
                self.holdingDrafts = copy
            }
        )
    }

    /// Auto-fetch CNY rates for every non-CNY currency in the portfolio (能力1).
    public func refreshFxRates() async {
        statusMessage = "正在更新汇率…"
        var currencies = Set(perspectives.map { $0.currency }).filter { $0 != "CNY" }
        if currencies.isEmpty { currencies = ["USD", "HKD", "JPY", "SGD"] }
        let src = YahooFinanceSource()
        var newRates: [FxRate] = []
        var failed: [String] = []
        for ccy in currencies.sorted() {
            do {
                let info = try await src.lookup(symbol: ccy + "CNY=X")
                guard let price = info.price, price > 0 else { failed.append(ccy); continue }
                newRates.append(FxRate(currency: ccy, rateToCny: price,
                                       asOfDate: Self.todayString(), source: "yahoo"))
            } catch { failed.append(ccy) }
        }
        if !newRates.isEmpty { try? db.upsertFxRates(newRates) }
        loadAll()
        statusMessage = "汇率更新完成" + (failed.isEmpty ? "" : "，失败 \(failed.count) 个币种")
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

    /// Extract a 6-digit fund code from a ticker/key (e.g. "110035.CN_Fund" / "159307.SZ" → "110035" / "159307").
    /// Bare 6-digit codes are also treated as funds. Returns nil otherwise (Yahoo symbols).
    private static func fundCode(_ ticker: String) -> String? {
        let pattern = #"^(\d{6})(\.(CN_Fund|SZ|SH))?$"#
        guard let re = try? NSRegularExpression(pattern: pattern) else { return nil }
        let ns = ticker as NSString
        let m = re.firstMatch(in: ticker, range: NSRange(location: 0, length: ns.length))
        guard let m, m.numberOfRanges > 1, m.range(at: 1).location != NSNotFound else { return nil }
        return ns.substring(with: m.range(at: 1))
    }

    /// Resolve an asset to (data source, symbol). 境内基金(6位代码) → 天天基金, 其余 → Yahoo.
    private func resolveDataSource(asset: Asset) -> (source: any DataSource, symbol: String)? {
        let ticker = asset.ticker ?? asset.key
        if let code = Self.fundCode(ticker) {
            return (EastmoneySource(), code)
        }
        if let ref = AssetCatalog.ref(for: asset.key) {
            return ref.source == .fund ? (EastmoneySource(), ref.symbol) : (YahooFinanceSource(), ref.symbol)
        }
        if !ticker.isEmpty {
            return (YahooFinanceSource(), ticker)
        }
        return nil
    }

    public func refreshPrices() async {
        statusMessage = "正在更新行情…"
        var updated = 0
        var failed: [String] = []
        var newQuotes: [Quote] = []
        let assets = (try? db.fetchAssets()) ?? []
        let assetByKey = Dictionary(uniqueKeysWithValues: assets.map { ($0.key, $0) })
        for row in perspectives {
            guard let a = assetByKey[row.assetKey],
                  let (source, symbol) = resolveDataSource(asset: a) else { continue }
            do {
                // History (cumulative NAV for funds, K-line for stocks) → prices table (chart).
                let hist = try await source.fetchHistory(symbol: symbol)
                let points = hist.map { PricePoint(assetKey: row.assetKey, date: $0.date, close: $0.close, currency: $0.currency) }
                try db.upsertPrices(points)
                updated += points.count
                // Quote (unit NAV for funds, latest price for stocks) → quotes table (market value).
                if let q = try? await source.fetchQuote(symbol: symbol) {
                    newQuotes.append(Quote(symbol: row.assetKey, price: q.price,
                                           currency: q.currency, date: q.date, source: q.source))
                }
            } catch {
                failed.append(row.assetKey)
            }
        }
        if !newQuotes.isEmpty { try? db.upsertQuotes(newQuotes) }
        statusMessage = "行情更新完成：\(updated) 个数据点" + (failed.isEmpty ? "" : "，失败 \(failed.count) 个")
        loadAll()
        await refreshMacroRates()
    }

    // MARK: 能力2 — macro rates (动态 RF)

    /// Fetch CN/US 10-year treasury yields via akshare (Python sidecar) and cache
    /// into macro_rates. RF = ov_w * us_10y + dom_w * cn_10y replaces params.py's
    /// hardcoded 0.025 — reflects the portfolio's actual domestic/overseas opportunity cost.
    /// akshare.bond_zh_us_rate returns 百分点 (e.g. 4.69); the script converts to fraction.
    public func refreshMacroRates() async {
        let sidecar = optimizer.sidecar
        let scriptPath = sidecar.scriptURL("fetch_macro_rates.py").path
        guard FileManager.default.fileExists(atPath: sidecar.interpreterPath),
              FileManager.default.fileExists(atPath: scriptPath) else { return }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: sidecar.interpreterPath)
        process.arguments = [scriptPath]
        if let cwd = sidecar.currentDirectoryURL { process.currentDirectoryURL = cwd }
        process.environment = ProcessInfo.processInfo.environment
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = Pipe()
        do {
            try process.run()
            process.waitUntilExit()
            guard process.terminationStatus == 0,
                  let data = try? pipe.fileHandleForReading.readToEnd(),
                  let raw = String(data: data, encoding: .utf8),
                  let json = raw.data(using: .utf8),
                  let obj = try? JSONSerialization.jsonObject(with: json) as? [String: Any] else { return }
            if let cn = obj["cn_10y"] as? Double,
               let us = obj["us_10y"] as? Double,
               let date = obj["date"] as? String {
                try? db.upsertMacroRate("cn_10y", value: cn, asOfDate: date, source: "akshare")
                try? db.upsertMacroRate("us_10y", value: us, asOfDate: date, source: "akshare")
            }
        } catch {
            // macro rates are best-effort; failure falls back to params.py hardcoded RF.
        }
    }

    // MARK: 能力1 — 比较基准 (沪深300 + 标普500 加权, 与优化器基准一致)

    /// 境内/境外池权重 — 来自资产管理的实时统计 (每个标的自身的 pool 归属),
    /// 不再使用 .numbers 提取文件的预填比例; 无持仓数据时回退 50/50.
    public var livePoolWeights: (domestic: Double, overseas: Double) {
        guard let alloc = allocation, alloc.totalValue > 0 else { return (0.5, 0.5) }
        let dom = alloc.domesticValue / alloc.totalValue
        let ov = alloc.overseasValue / alloc.totalValue
        // 跨池部分不计入基准 (相当于现金), 剩余比例归一化使用.
        let used = dom + ov
        guard used > 0 else { return (0.5, 0.5) }
        return (dom / used, ov / used)
    }

    /// Fetch and store benchmark history (SPY + 000300.SS weighted by the live
    /// domestic/overseas pool split from 资产管理). Falls back to 50/50 when no data.
    public func fetchBenchmark() async {
        let w = livePoolWeights
        let domW = w.domestic
        let overW = w.overseas
        let src = YahooFinanceSource()
        var spyPts: [PricePoint] = []
        var csiPts: [PricePoint] = []
        do { spyPts = try await src.fetchHistory(symbol: "SPY") } catch {}
        do { csiPts = try await src.fetchHistory(symbol: "000300.SS") } catch {}
        // Try alternative CSI300 symbol if 000300.SS fails
        if csiPts.isEmpty { do { csiPts = try await src.fetchHistory(symbol: "510300.SS") } catch {} }
        guard !spyPts.isEmpty || !csiPts.isEmpty else { return }
        // Build union of dates, forward-filled, rebased to 1.0 at first observation, weighted.
        var dateSet = Set<String>()
        var spyMap: [String: Double] = [:]
        var csiMap: [String: Double] = [:]
        if let base = spyPts.first?.close, base > 0 {
            for p in spyPts { spyMap[p.date] = p.close / base; dateSet.insert(p.date) }
        }
        if let base = csiPts.first?.close, base > 0 {
            for p in csiPts { csiMap[p.date] = p.close / base; dateSet.insert(p.date) }
        }
        let dates = dateSet.sorted()
        var lastSpy = 1.0, lastCsi = 1.0
        var allPts: [PerformancePoint] = []
        for d in dates {
            if let v = spyMap[d] { lastSpy = v }
            if let v = csiMap[d] { lastCsi = v }
            let weighted = lastCsi * domW + lastSpy * overW
            allPts.append(PerformancePoint(date: d, value: weighted))
        }
        // Trim to last 3 years to align with fetchPerformance(lookbackYears: 3).
        // Rebase the trimmed series to 1.0 at its first point so the benchmark
        // starts at the same baseline as the portfolio series.
        let cutoff = Calendar.current.date(byAdding: .year, value: -3, to: Date()) ?? Date()
        let fmt = DateFormatters.utcDay
        let cutoffStr = fmt.string(from: cutoff)
        var trimmed = allPts.filter { $0.date >= cutoffStr }
        if let base = trimmed.first?.value, base > 0 {
            trimmed = trimmed.map { PerformancePoint(date: $0.date, value: $0.value / base) }
        }
        benchmarkPoints = trimmed
    }

    // MARK: 能力2 — optimizer

    /// Copy of extract_app.json with pool stats + us_equity + domestic_holdings + rf
    /// overwritten by LIVE 资产管理/资产透视 statistics — 优化器不再使用 .numbers 冻结快照.
    /// - 境内/境外权重 = 占总资产比例 (未归一化), 跨池余量由优化器自由分配.
    /// - us_equity.holdings = 实时美股持仓 (替代 .numbers 导入日冻结值 → O_US_CORE 聚合 mu/vol + stage2 优先级).
    /// - domestic_holdings = 实时境内基金持仓 (动态锚定, 替代 params.py 写死的 fund_code).
    /// - rf = 池比例加权中美10年国债收益率 (替代 params.py 写死的 0.025).
    private func liveExtractURL() -> URL? {
        let src = AppPaths.extractJSONURL()
        guard FileManager.default.fileExists(atPath: src.path),
              let data = try? Data(contentsOf: src),
              var obj = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return nil }

        // Pool stats (R5+): live 境内/境外 比例.
        var domW = 0.0, ovW = 0.0
        if let alloc = allocation, alloc.totalValue > 0 {
            obj["pool_mode"] = "app_live"
            obj["domestic_value"] = alloc.domesticValue
            obj["overseas_value"] = alloc.overseasValue
            obj["total_value"] = alloc.totalValue
            domW = alloc.domesticValue / alloc.totalValue
            ovW = alloc.overseasValue / alloc.totalValue
            obj["domestic_weight"] = domW
            obj["overseas_weight"] = ovW
        }

        // us_equity: live 美股持仓 (替代 .numbers 冻结快照).
        let assets = (try? db.fetchAssets()) ?? []
        let byKey = Dictionary(uniqueKeysWithValues: assets.map { ($0.key, $0) })
        let usRows = perspectives.filter { $0.assetClass == "us_equity" && $0.valueCny > 0 }
        if !usRows.isEmpty {
            let totalUs = usRows.reduce(0.0) { $0 + $1.valueCny }
            let holdings = usRows.sorted { $0.valueCny > $1.valueCny }.map { row -> [String: Any] in
                let ticker = (byKey[row.assetKey]?.ticker) ?? row.assetKey
                return [
                    "ticker": ticker,
                    "name": row.name,
                    "value_cny": row.valueCny,
                    "weight": totalUs > 0 ? row.valueCny / totalUs : 0,
                ]
            }
            obj["us_equity"] = ["total_value": totalUs, "holdings": holdings] as [String: Any]
        }
        // 美股为空时跳过覆盖 → 保留 .numbers 原值 (Python us_core_params 空权重会 div-zero, 不可覆盖为空).

        // domestic_holdings: live 境内基金持仓 (动态锚定, 替代 params.py 写死的 fund_code).
        let domFunds: [[String: Any]] = perspectives.filter { $0.pool == .domestic }
            .compactMap { row -> [String: Any]? in
                let a = byKey[row.assetKey]
                let raw = a?.ticker ?? row.assetKey
                guard let code = Self.fundCode(raw) else { return nil }  // 只取 6 位基金代码
                return ["code": code, "name": row.name, "value_cny": row.valueCny]
            }
        if !domFunds.isEmpty { obj["domestic_holdings"] = domFunds }

        // RF: 池比例加权中美10年国债收益率 (替代 params.py 写死 0.025).
        if let cn = try? db.fetchMacroRate("cn_10y")?.value,
           let us = try? db.fetchMacroRate("us_10y")?.value,
           domW > 0 || ovW > 0 {
            let rf = ovW * us + domW * cn
            obj["rf"] = rf
        }

        let dst = src.deletingLastPathComponent().appendingPathComponent("extract_live.json")
        guard let out = try? JSONSerialization.data(withJSONObject: obj) else { return nil }
        do { try out.write(to: dst) } catch { return nil }
        return dst
    }

    public func runOptimization() {
        guard !isOptimizing else { return }
        let extract = liveExtractURL() ?? AppPaths.extractJSONURL()
        guard FileManager.default.fileExists(atPath: extract.path) else {
            optimizeError = "未找到提取文件：\(extract.path)。请先导入 .numbers 数据。"
            return
        }
        isOptimizing = true
        optimizeSteps = []
        optimizeError = nil
        lastOptimization = nil

        do {
            _ = try optimizer.start(extractJSON: extract, targetReturn: targetReturn)
            pollTask = Task { [weak self] in
                guard let self else { return }
                while self.optimizer.isRunning {
                    let fresh = self.optimizer.pollSteps()
                    if !fresh.isEmpty { self.optimizeSteps.append(contentsOf: fresh) }
                    try? await Task.sleep(for: .milliseconds(300))
                }
                do {
                    let out = try self.optimizer.finalize()
                    self.lastOptimization = out.result
                    self.optimizeError = out.error
                    if !out.steps.isEmpty { self.optimizeSteps = out.steps }
                } catch {
                    self.optimizeError = String(describing: error)
                }
                self.isOptimizing = false
                self.loadAll()
                Task { await self.fetchBenchmark() }  // refresh benchmark with new weights
            }
        } catch {
            optimizeError = String(describing: error)
            isOptimizing = false
        }
    }

    public func cancelOptimization() {
        pollTask?.cancel()
        pollTask = nil
        // note: the subprocess is not killed here; it finishes on its own and is finalized lazily
        isOptimizing = false
    }

    // MARK: 能力3 — PDF export

    public func exportPDF(to url: URL) throws {
        guard let alloc = allocation, let summary = performanceSummary else {
            throw PDFError.context
        }
        try PDFExporter.writeReport(
            to: url,
            allocation: alloc,
            performance: summary,
            rows: perspectives,
            generatedAt: DateFormatters.nowISO())
    }

    // MARK: 能力3 — 备份数据导入/导出 (可移植 JSON)

    public func exportBackup(to url: URL) throws {
        let bm = BackupManager(db: db, backupDir: AppPaths.backupsURL())
        try bm.exportJSON(to: url)
    }

    public func importBackup(from url: URL) async throws {
        let bm = BackupManager(db: db, backupDir: AppPaths.backupsURL())
        try bm.importJSON(from: url)
        loadAll()
        await startupRefresh()
    }
}
