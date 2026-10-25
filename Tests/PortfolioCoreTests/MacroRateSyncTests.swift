import XCTest
@testable import PortfolioCore

/// `MacroRateSync`：解析是纯函数、子进程在后台线程跑（解析 + 落库 + 失败静默）。
@MainActor
final class MacroRateSyncTests: XCTestCase {

    func testParsesCompletePayload() {
        let raw = #"{"cn_10y":0.018,"us_10y":0.042,"date":"2026-01-30"}"#
        let rates = MacroRateSync.parse(raw)
        XCTAssertEqual(rates, MacroRates(cn10y: 0.018, us10y: 0.042, date: "2026-01-30"))
    }

    func testRejectsMissingFields() {
        XCTAssertNil(MacroRateSync.parse(#"{"cn_10y":0.018,"date":"2026-01-30"}"#))
        XCTAssertNil(MacroRateSync.parse("not json"))
    }

    func testRefreshWritesBothRates() async throws {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("pm-macro-" + UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        // 脚本占位：refresh 只做存在性检查，真正的调用面是注入的 run。
        FileManager.default.createFile(atPath: dir.appendingPathComponent("fetch_macro_rates.py").path,
                                       contents: Data())
        let db = try Database(path: dir.appendingPathComponent("t.db").path)
        let config = SidecarConfig(interpreterPath: "/bin/ls", scriptsDir: dir,
                                   currentDirectoryURL: nil)

        await MacroRateSync.refresh(config: config, into: db) { _ in
            #"{"cn_10y":0.019,"us_10y":0.043,"date":"2026-02-02"}"#
        }

        XCTAssertEqual(try db.fetchMacroRate("cn_10y")?.value, 0.019)
        XCTAssertEqual(try db.fetchMacroRate("us_10y")?.value, 0.043)
        XCTAssertEqual(try db.fetchMacroRate("cn_10y")?.source, "akshare")
    }

    func testRefreshIsSilentWhenRunnerFailsOrOutputInvalid() async throws {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("pm-macro-" + UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        FileManager.default.createFile(atPath: dir.appendingPathComponent("fetch_macro_rates.py").path,
                                       contents: Data())
        let db = try Database(path: dir.appendingPathComponent("t.db").path)
        let config = SidecarConfig(interpreterPath: "/bin/ls", scriptsDir: dir,
                                   currentDirectoryURL: nil)

        await MacroRateSync.refresh(config: config, into: db) { _ in
            struct Boom: Error {}
            throw Boom()
        }
        XCTAssertNil(try db.fetchMacroRate("cn_10y"))

        await MacroRateSync.refresh(config: config, into: db) { _ in "broken" }
        XCTAssertNil(try db.fetchMacroRate("cn_10y"))
    }
}
