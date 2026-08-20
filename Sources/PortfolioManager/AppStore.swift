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
    public var value: Double
    public var currency: String
    public init(quantity: Double = 0, costBasis: Double = 0, value: Double = 0, currency: String = "CNY") {
        self.quantity = quantity
        self.costBasis = costBasis
        self.value = value
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

    // 模块2
    public var perspectives: [AssetPerspectiveRow] = []
    /// Editing drafts (assetKey -> draft). Populated on load; saved via savePerspectives().
    public var holdingDrafts: [String: HoldingDraft] = [:]

    // 能力4
    public var financials: [Financial] = []

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
                                           value: $0.value, currency: $0.currency))
            })
            fxRates = try db.fetchFxRates()
            financials = try repository.fetchFinancialComparison()
            lastUpdated = ISO8601DateFormatter().string(from: Date())
            statusMessage = nil
        } catch {
            statusMessage = "加载失败: \(error)"
        }
    }

    // MARK: 模块2 / 能力4 — editing & save

    public var hasUnsavedChanges: Bool {
        for (key, draft) in holdingDrafts {
            guard let row = perspectives.first(where: { $0.assetKey == key }) else { continue }
            if draft.value != row.value || draft.quantity != row.quantity || draft.costBasis != row.costBasis || draft.currency != row.currency {
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
                                     costBasis: draft.costBasis, value: draft.value, currency: draft.currency)
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

    public func upsertFinancial(_ f: Financial) {
        do {
            try db.upsertFinancials([f])
            loadAll()
            statusMessage = "已保存财务报表"
        } catch {
            statusMessage = "保存失败: \(error)"
        }
    }

    public func deleteFinancial(id: Int64) {
        do {
            try db.deleteFinancial(id: id)
            loadAll()
            statusMessage = "已删除"
        } catch {
            statusMessage = "删除失败: \(error)"
        }
    }

    // MARK: 能力2 — 标的增删 + 联网校验 + 汇率

    /// Currencies offered in the pickers.
    public static let currencyOptions = ["CNY", "USD", "HKD", "JPY", "SGD", "EUR", "GBP", "AUD", "CAD", "CHF"]

    private static func todayString() -> String {
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd"; f.timeZone = TimeZone(identifier: "UTC")
        return f.string(from: Date())
    }

    /// Validate a ticker online (Yahoo Finance); returns resolved currency/name or a failure message.
    public func lookupSymbol(_ symbol: String) async -> (valid: Bool, currency: String, name: String?, message: String) {
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
            let asset = Asset(key: key, name: name, ticker: ticker, market: market,
                              assetClass: assetClass, pool: pool, currency: currency, source: "manual")
            try db.insertAsset(asset)
            try db.upsertHoldings([Holding(assetKey: key, quantity: 0, costBasis: 0,
                                           value: 0, currency: currency,
                                           asOfDate: Self.todayString())])
            loadAll()
            statusMessage = "已添加 \(name)"
        } catch {
            statusMessage = "添加失败: \(error)"
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

    public func refreshPrices() async {
        statusMessage = "正在更新行情…"
        var updated = 0
        var failed: [String] = []
        let assets = (try? db.fetchAssets()) ?? []
        let assetByKey = Dictionary(uniqueKeysWithValues: assets.map { ($0.key, $0) })
        for row in perspectives {
            let ref = AssetCatalog.ref(for: row.assetKey)
            let source: any DataSource
            let symbol: String
            if let ref, ref.source == .fund {
                source = EastmoneySource(); symbol = ref.symbol
            } else if let ref {
                source = YahooFinanceSource(); symbol = ref.symbol
            } else if let a = assetByKey[row.assetKey], let t = a.ticker, !t.isEmpty {
                source = YahooFinanceSource(); symbol = t
            } else {
                continue
            }
            do {
                let hist = try await source.fetchHistory(symbol: symbol)
                let points = hist.map { PricePoint(assetKey: row.assetKey, date: $0.date, close: $0.close, currency: $0.currency) }
                try db.upsertPrices(points)
                updated += points.count
            } catch {
                failed.append(row.assetKey)
            }
        }
        statusMessage = "行情更新完成：\(updated) 个数据点" + (failed.isEmpty ? "" : "，失败 \(failed.count) 个")
        loadAll()
    }

    // MARK: 能力2 — optimizer

    public func runOptimization() {
        guard !isOptimizing else { return }
        let extract = AppPaths.extractJSONURL()
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
            generatedAt: ISO8601DateFormatter().string(from: Date()))
    }
}
