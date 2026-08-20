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

let args = CommandLine.arguments
if args.count >= 2 && args[1] == "--self-test" {
    exit(selfTest())
}
guard args.count >= 2 else {
    print("用法: pm-cli <portfolio_result.json>  或  pm-cli --self-test")
    exit(1)
}
let url = URL(fileURLWithPath: args[1])
do {
    let result = try ResultImporter.decode(url: url)
    print(ResultImporter.summarize(result))
} catch {
    FileHandle.standardError.write("解析失败: \(error)\n".data(using: .utf8)!)
    exit(1)
}
