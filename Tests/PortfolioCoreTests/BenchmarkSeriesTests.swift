import XCTest
@testable import PortfolioCore

/// `BenchmarkSeries`：比较基准线的纯合成（并集 + 前向填充 + 归一 + 裁剪）。
final class BenchmarkSeriesTests: XCTestCase {

    private var now: Date { date("2026-06-30 00:00:00") }
    private let calendar: Calendar = {
        var c = Calendar(identifier: .gregorian)
        c.timeZone = TimeZone(identifier: "UTC")!
        return c
    }()

    func testForwardFillsMissingLegAndWeights() {
        let spy = [point("2026-01-01", 100), point("2026-01-03", 110)]
        let csi = [point("2026-01-02", 200), point("2026-01-03", 202)]

        let series = BenchmarkSeries.compose(spy: spy, csi: csi,
                                             domesticWeight: 0.5, overseasWeight: 0.5,
                                             now: now, calendar: calendar)

        XCTAssertEqual(series.map { $0.date }, ["2026-01-01", "2026-01-02", "2026-01-03"])
        XCTAssertEqual(series[0].value, 1.0, accuracy: 1e-12)
        XCTAssertEqual(series[1].value, 1.0, accuracy: 1e-12, "缺失的腿按前值（1.0）参与")
        XCTAssertEqual(series[2].value, 0.5 * 1.01 + 0.5 * 1.1, accuracy: 1e-12)
    }

    func testTrimsPointsOlderThanLookbackAndRebases() throws {
        let spy = [point("2020-01-02", 50), point("2026-01-02", 100), point("2026-01-03", 110)]
        let series = BenchmarkSeries.compose(spy: spy, csi: [],
                                             domesticWeight: 0, overseasWeight: 1,
                                             lookbackYears: 3, now: now, calendar: calendar)
        XCTAssertEqual(series.first?.date, "2026-01-02", "早于 3 年的点被裁掉")
        XCTAssertEqual(try XCTUnwrap(series.first).value, 1.0, accuracy: 1e-12,
                       "裁剪后按首点重新归一")
        XCTAssertEqual(try XCTUnwrap(series.last).value, 1.1, accuracy: 1e-12)
    }

    func testSingleLegOverseasOnly() {
        let spy = [point("2026-01-01", 100), point("2026-01-02", 110)]
        let series = BenchmarkSeries.compose(spy: spy, csi: [],
                                             domesticWeight: 0.4, overseasWeight: 0.6,
                                             now: now, calendar: calendar)
        XCTAssertEqual(series.map { $0.value }, [1.0, 0.4 + 0.6 * 1.1])
    }

    func testSingleLegDomesticOnly() {
        let csi = [point("2026-01-01", 100), point("2026-01-02", 110)]
        let series = BenchmarkSeries.compose(spy: [], csi: csi,
                                             domesticWeight: 0.4, overseasWeight: 0.6,
                                             now: now, calendar: calendar)
        XCTAssertEqual(series.map { $0.value }, [1.0, 0.4 * 1.1 + 0.6])
    }

    func testEmptyInputsReturnEmpty() {
        XCTAssertTrue(BenchmarkSeries.compose(spy: [], csi: [],
                                              domesticWeight: 0.5, overseasWeight: 0.5,
                                              now: now, calendar: calendar).isEmpty)
    }

    private func point(_ date: String, _ close: Double) -> PricePoint {
        PricePoint(assetKey: "SYN", date: date, close: close, currency: "USD")
    }

    private func date(_ text: String) -> Date {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd HH:mm:ss"
        f.timeZone = TimeZone(identifier: "UTC")!
        return f.date(from: text)!
    }
}
