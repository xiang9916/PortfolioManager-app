import Foundation
import SwiftUI
import PortfolioCore

/// App-level paths. Dev layout points at the repo tree; Phase 8 packaging will
/// relocate the DB + vendored optimizer into the .app bundle / Application Support.
public enum AppPaths {
    /// Database location. Prefer an existing dev DB (repo tmp/), else Application Support.
    public static func databaseURL() -> URL {
        let cwd = FileManager.default.currentDirectoryPath
        let dev = URL(fileURLWithPath: cwd).appendingPathComponent("tmp/portfolio.db")
        if FileManager.default.fileExists(atPath: dev.path) {
            return dev
        }
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
        let dir = support.appendingPathComponent("PortfolioManager", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("portfolio.db")
    }

    /// Vendored optimizer scripts directory (dev: repo Optimizer/scripts).
    public static func scriptsURL() -> URL {
        let cwd = FileManager.default.currentDirectoryPath
        return URL(fileURLWithPath: cwd).appendingPathComponent("Optimizer/scripts", isDirectory: true)
    }

    /// Python interpreter inside the vendored venv.
    public static func interpreterPath() -> String {
        let cwd = FileManager.default.currentDirectoryPath
        return URL(fileURLWithPath: cwd).appendingPathComponent("Optimizer/.venv/bin/python3").path
    }

    /// Extracted .numbers state (input to the optimizer).
    public static func extractJSONURL() -> URL {
        let cwd = FileManager.default.currentDirectoryPath
        return URL(fileURLWithPath: cwd).appendingPathComponent("tmp/extract_app.json")
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
    public let db: Database
    public let repository: Repository
    public let optimizer: OptimizationService

    // 模块1 / 能力1
    public var allocation: AllocationSnapshot?
    public var performancePoints: [PerformancePoint] = []
    public var performanceSummary: PerformanceSummary?

    // 模块2
    public var perspectives: [AssetPerspectiveRow] = []

    // 能力4
    public var financials: [Financial] = []

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
            financials = try repository.fetchFinancialComparison()
            lastUpdated = ISO8601DateFormatter().string(from: Date())
            statusMessage = nil
        } catch {
            statusMessage = "加载失败: \(error)"
        }
    }

    // MARK: 能力1 — data refresh (auto-fetch public quotes)

    public func refreshPrices() async {
        statusMessage = "正在更新行情…"
        var updated = 0
        var failed: [String] = []
        for row in perspectives {
            guard let ref = AssetCatalog.ref(for: row.assetKey) else { continue }
            let source: any DataSource = ref.source == .fund ? EastmoneySource() : YahooFinanceSource()
            do {
                let hist = try await source.fetchHistory(symbol: ref.symbol)
                let points = hist.map { PricePoint(assetKey: ref.key, date: $0.date, close: $0.close, currency: $0.currency) }
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
