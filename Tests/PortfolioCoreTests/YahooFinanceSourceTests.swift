import XCTest
import Foundation
@testable import PortfolioCore

/// Yahoo 历史序列的总回报口径（ADR 0005）：优先 adjclose，缺失时回落 close。
/// 全部使用虚构行情，不含任何真实持仓信息。
final class YahooFinanceSourceTests: XCTestCase {
    private func decode(_ json: String) throws -> YahooChartResponse {
        try JSONDecoder().decode(YahooChartResponse.self, from: Data(json.utf8))
    }

    /// 不含 adjclose 的响应头；尾部由各用例补齐。
    private let header = #"{"chart":{"result":[{"meta":{"currency":"USD"},"timestamp":[1704067200, 1704153600],"indicators":{"quote":[{"close":[100.0,110.0]}]"#

    func testHistoryUsesAdjustedCloseWhenPresent() throws {
        let json = header + #","adjclose":[{"adjclose":[99.0,109.0]}]}}]}}"#
        let rows = YahooFinanceSource.historyPoints(from: try decode(json), symbol: "FICT")
        XCTAssertEqual(rows.map { $0.close }, [99.0, 109.0])
        XCTAssertEqual(rows.map { $0.currency }, ["USD", "USD"])
    }

    func testHistoryFallsBackToCloseWhenAdjustedMissing() throws {
        let json = header + #"}}]}}"#
        let rows = YahooFinanceSource.historyPoints(from: try decode(json), symbol: "FICT")
        XCTAssertEqual(rows.map { $0.close }, [100.0, 110.0])
    }

    /// 开/高/低 是原始价，close 是复权价 → 必须按当日 adjclose/close 因子换算到同一口径。
    func testHistoryScalesOHLCToAdjustedClose() throws {
        let json = #"{"chart":{"result":[{"meta":{"currency":"USD"},"timestamp":[1704067200, 1704153600],"indicators":{"quote":[{"close":[100.0,110.0],"open":[99.0,109.0],"high":[101.0,111.0],"low":[98.0,108.0]}],"adjclose":[{"adjclose":[50.0,55.0]}]}}]}}"#
        let rows = YahooFinanceSource.historyPoints(from: try decode(json), symbol: "FICT")
        XCTAssertEqual(rows.map { $0.close }, [50.0, 55.0])
        XCTAssertEqual(rows.map { $0.open ?? 0 }, [49.5, 54.5])
        XCTAssertEqual(rows.map { $0.high ?? 0 }, [50.5, 55.5])
        XCTAssertEqual(rows.map { $0.low ?? 0 }, [49.0, 54.0])
    }

    /// 响应里没有开/高/低（或某天缺失）→ 保持 nil，交给下游按「无日内价」降级。
    func testHistoryLeavesOHLCNilWhenAbsent() throws {
        let without = #"{"chart":{"result":[{"meta":{"currency":"USD"},"timestamp":[1704067200],"indicators":{"quote":[{"close":[100.0]}]}}]}}"#
        let row = YahooFinanceSource.historyPoints(from: try decode(without), symbol: "FICT").first
        XCTAssertNotNil(row)
        XCTAssertNil(row?.open)
        XCTAssertNil(row?.high)
        XCTAssertNil(row?.low)

        let partial = #"{"chart":{"result":[{"meta":{"currency":"USD"},"timestamp":[1704067200, 1704153600],"indicators":{"quote":[{"close":[100.0,110.0],"open":[99.0,109.0],"high":[101.0,null],"low":[98.0,108.0]}]}}]}}"#
        let rows = YahooFinanceSource.historyPoints(from: try decode(partial), symbol: "FICT")
        XCTAssertEqual(rows.count, 2)
        XCTAssertEqual(rows[0].high ?? 0, 101.0)
        XCTAssertNil(rows[1].high)
    }

    func testHistorySkipsNilPoints() throws {
        let json = #"{"chart":{"result":[{"meta":{"currency":"USD"},"timestamp":[1704067200, 1704153600],"indicators":{"quote":[{"close":[100.0,null]}],"adjclose":[{"adjclose":[99.0,null]}]}}]}}"#
        let rows = YahooFinanceSource.historyPoints(from: try decode(json), symbol: "FICT")
        XCTAssertEqual(rows.map { $0.close }, [99.0])
    }
}
