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

    func testHistorySkipsNilPoints() throws {
        let json = #"{"chart":{"result":[{"meta":{"currency":"USD"},"timestamp":[1704067200, 1704153600],"indicators":{"quote":[{"close":[100.0,null]}],"adjclose":[{"adjclose":[99.0,null]}]}}]}}"#
        let rows = YahooFinanceSource.historyPoints(from: try decode(json), symbol: "FICT")
        XCTAssertEqual(rows.map { $0.close }, [99.0])
    }
}
