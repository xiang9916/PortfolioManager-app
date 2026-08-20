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

let args = CommandLine.arguments
if args.count >= 2 && args[1] == "--self-test" { exit(selfTest()) }
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
print("  pm-cli --self-test")
exit(1)
