import Foundation
import PortfolioCore

func selfTest() -> Int32 {
    let json = """
    {"generated_at":"2026-08-19T11:39:28Z",
     "portfolio":{"expected_return":0.1,"volatility":0.123935,"sharpe":0.605155,"worst_year_95":-0.142913},
     "assets":[{"key":"SPMO","name":"Invesco S&P 500 Momentum ETF","weight":0.341465,"expected_return":0.1204,"volatility":0.2042,"sharpe":0.467189}],
     "benchmark":{"expected_return":0.084597,"volatility":0.140774,"sharpe":0.423355,"worst_year_95":-0.19132,"portfolio_alpha":0.025423,"portfolio_beta":0.831857,"volatility_reduction":0.016839,"worst_year_improvement":0.048407,"weights":{"domestic_CSI300":0.2197,"overseas_SP500":0.7803}}}
    """
    do {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let r = try decoder.decode(OptimizationResult.self, from: json.data(using: .utf8)!)
        let ok = r.assets.count == 1
            && r.assets[0].key == "SPMO"
            && abs(r.portfolio.expectedReturn - 0.1) < 1e-9
            && abs((r.benchmark?.portfolioAlpha ?? 0) - 0.025423) < 1e-9
        print(ok ? "SELF-TEST PASS" : "SELF-TEST FAIL")
        return ok ? 0 : 1
    } catch {
        print("SELF-TEST FAIL: \(error)")
        return 1
    }
}

func sidecar() -> PythonSidecar {
    let fm = FileManager.default
    let cwd = fm.currentDirectoryPath
    let scripts = URL(fileURLWithPath: cwd).appendingPathComponent("Optimizer/scripts")
    let interpreter = URL(fileURLWithPath: cwd).appendingPathComponent("Optimizer/.venv/bin/python3").path
    return PythonSidecar(interpreterPath: interpreter, scriptsDir: scripts,
                         currentDirectoryURL: URL(fileURLWithPath: cwd))
}

func runExtract(numbersPath: String) -> Int32 {
    do {
        let outPath = "tmp/extract_app.json"
        try FileManager.default.createDirectory(atPath: "tmp", withIntermediateDirectories: true)
        let sc = sidecar()
        try sc.run(script: "extract_portfolio.py", args: [numbersPath, "--json", outPath])
        let data = try Data(contentsOf: URL(fileURLWithPath: outPath))
        if let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any] {
            print("提取成功")
            print("  总资产: \(obj["total_value"] ?? "?")")
            print("  境内: \(obj["domestic_value"] ?? "?")  境外: \(obj["overseas_value"] ?? "?")")
            print("  pool_mode: \(obj["pool_mode"] ?? "?")")
            if let warnings = obj["warnings"] as? [String] {
                for w in warnings { print("  警告: \(w)") }
            }
        }
        return 0
    } catch {
        FileHandle.standardError.write("提取失败: \(error)\n".data(using: .utf8)!)
        return 1
    }
}

// Swift 6 strict-concurrency-safe async bridge for a CLI.
final class AsyncResult: @unchecked Sendable {
    var error: String?
}

func runAsync(_ body: @escaping @Sendable () async throws -> Void) -> Int32 {
    let sem = DispatchSemaphore(value: 0)
    let result = AsyncResult()
    Task {
        do { try await body() }
        catch { result.error = String(describing: error) }
        sem.signal()
    }
    sem.wait()
    if let e = result.error {
        FileHandle.standardError.write("错误: \(e)\n".data(using: .utf8)!)
        return 1
    }
    return 0
}

let args = CommandLine.arguments
if args.count >= 2 && args[1] == "--self-test" { exit(selfTest()) }
if args.count >= 3 && args[1] == "fetch" {
    let sourceName = args[2]
    let symbol = args.count >= 4 ? args[3] : "SPMO"
    exit(runAsync {
        let source: any DataSource = sourceName == "fund" ? EastmoneySource() : YahooFinanceSource()
        let hist = try await source.fetchHistory(symbol: symbol)
        print("来源 \(source.name)  标的 \(symbol)  共 \(hist.count) 个数据点")
        for p in hist.suffix(5) { print("  \(p.date)  \(p.close)") }
    })
}
if args.count >= 2 && args[1] == "refresh" {
    let dbPath = args.count >= 3 ? args[2] : "tmp/portfolio.db"
    exit(runAsync {
        let db = try Database(path: dbPath)
        let keys = args.count >= 4 ? Array(args[3...]) : ["SPMO", "UNH", "GOOG", "O_GOLD", "O_BTC", "D_CN_CREDIT_BOND"]
        var total = 0
        for key in keys {
            guard let ref = AssetCatalog.ref(for: key) else { print("  未知标的: \(key)"); continue }
            let source: any DataSource = ref.source == .fund ? EastmoneySource() : YahooFinanceSource()
            let hist = try await source.fetchHistory(symbol: ref.symbol)
            let points = hist.map { PricePoint(assetKey: ref.key, date: $0.date, close: $0.close, currency: $0.currency) }
            try db.upsertPrices(points)
            total += points.count
            print("  \(ref.key) (\(ref.symbol)): \(points.count) 点")
        }
        try db.insertSnapshot(Snapshot(date: "2026-08-20", totalValue: 354290.32, domesticValue: 82051.27, overseasValue: 272239.04))
        print("共写入 \(total) 个价格点 → \(dbPath)")
        print("prices 表行数: \(db.count(table: "prices"))  snapshots 表行数: \(db.count(table: "snapshots"))")
    })
}
if args.count >= 2 && args[1] == "import" {
    let dbPath = args.count >= 3 ? args[2] : "tmp/portfolio.db"
    let extractPath = args.count >= 4 ? args[3] : "tmp/extract_app.json"
    do {
        let db = try Database(path: dbPath)
        try PortfolioImporter.importExtract(url: URL(fileURLWithPath: extractPath), into: db, asOfDate: "2026-08-20")
        print("导入完成: assets=" + String(db.count(table: "assets")) +
              " holdings=" + String(db.count(table: "holdings")) +
              " snapshots=" + String(db.count(table: "snapshots")))
        exit(0)
    } catch {
        FileHandle.standardError.write("导入失败: \(error)\n".data(using: .utf8)!)
        exit(1)
    }
}
if args.count >= 2 && (args[1] == "backup" || args[1] == "list" || args[1] == "restore" || args[1] == "export") {
    let dbPath = args.count >= 3 ? args[2] : "tmp/portfolio.db"
    let backupDir = URL(fileURLWithPath: "tmp/backups")
    do {
        let db = try Database(path: dbPath)
        let bm = BackupManager(db: db, backupDir: backupDir)
        switch args[1] {
        case "backup":
            let dest = try bm.createBackup()
            print("已备份: " + dest.lastPathComponent)
        case "list":
            let items = bm.listBackups()
            print("备份(" + String(items.count) + "):")
            for u in items { print("  " + u.lastPathComponent) }
        case "restore":
            guard args.count >= 4 else { print("用法: pm-cli restore <db> <backup.db>"); exit(1) }
            try bm.restore(from: URL(fileURLWithPath: args[3]))
            print("已回滚到: " + args[3])
        case "export":
            let out = URL(fileURLWithPath: args.count >= 4 ? args[3] : "tmp/export.json")
            try bm.exportJSON(to: out)
            print("已导出: " + out.path)
        default: break
        }
        exit(0)
    } catch {
        FileHandle.standardError.write("失败: \(error)\n".data(using: .utf8)!)
        exit(1)
    }
}
if args.count >= 2 && args[1] == "extract" {
    let numbersPath = args.count >= 3 ? args[2] : "../Finance/投资组合情况.numbers"
    exit(runExtract(numbersPath: numbersPath))
}
if args.count >= 2 && args[1] == "summarize" && args.count >= 3 {
    do {
        let r = try ResultImporter.decode(url: URL(fileURLWithPath: args[2]))
        print(ResultImporter.summarize(r))
        exit(0)
    } catch { FileHandle.standardError.write("解析失败: \(error)\n".data(using: .utf8)!); exit(1) }
}
print("用法:")
print("  pm-cli summarize <portfolio_result.json>")
print("  pm-cli extract [投资组合情况.numbers]")
print("  pm-cli fetch <yahoo|fund> [symbol]")
print("  pm-cli --self-test")
exit(1)
