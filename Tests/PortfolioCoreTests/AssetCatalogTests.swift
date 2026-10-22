import XCTest
@testable import PortfolioCore

/// 候选目录：IALT 是顶层 strategy 腿（ADR 0007），取数走 Yahoo 的 IALT 符号。
final class AssetCatalogTests: XCTestCase {
    func testIALTCatalogEntry() throws {
        let ref = try XCTUnwrap(AssetCatalog.ref(for: "O_IALT"))
        XCTAssertEqual(ref.source, .yahoo)
        XCTAssertEqual(ref.symbol, "IALT")
        XCTAssertEqual(ref.key, "O_IALT")
    }
}
