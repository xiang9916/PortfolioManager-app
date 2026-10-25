import XCTest
@testable import PortfolioCore

/// 候选目录的两层结构：modeled 与 Python SOURCES 对齐（由 Python 契约测试守住），
/// legacy 只为本机已有资产保留；两层都不允许重复 key。
final class AssetCatalogTests: XCTestCase {
    /// IALT 是顶层 strategy 腿（ADR 0007），取数走 Yahoo 的 IALT 符号。
    func testIALTCatalogEntry() throws {
        let ref = try XCTUnwrap(AssetCatalog.ref(for: "O_IALT"))
        XCTAssertEqual(ref.source, .yahoo)
        XCTAssertEqual(ref.symbol, "IALT")
        XCTAssertEqual(ref.key, "O_IALT")
    }

    /// Python 模型里有、Swift 目录里曾缺失的 4 个 key：必须能按 key 解析（符号正确性由契约测试守住）。
    func testModeledCatalogCoversPythonOnlyKeys() {
        for key in ["D_CN_QDII_STOCK", "D_CN_QDII_STABLE", "D_CN_QDII_COMMODITY", "D_GREATER_CN"] {
            XCTAssertNotNil(AssetCatalog.ref(for: key), key)
            XCTAssertTrue(AssetCatalog.modeled.contains { $0.key == key }, key)
        }
    }

    func testCatalogKeysAreUniqueAndTiersDisjoint() {
        let all = AssetCatalog.refs.map(\.key)
        XCTAssertEqual(all.count, Set(all).count, "目录 key 不得重复")
        let modeled = Set(AssetCatalog.modeled.map(\.key))
        let legacy = Set(AssetCatalog.legacy.map(\.key))
        XCTAssertTrue(modeled.isDisjoint(with: legacy), "modeled 与 legacy 不得重叠")
    }

    /// 旧 key 仍要能刷新行情（已有本地资产/旧备份的兼容面）。
    func testLegacyKeysStillResolve() {
        XCTAssertNotNil(AssetCatalog.ref(for: "D_CN_QDII_GLOBAL"))
        XCTAssertNotNil(AssetCatalog.ref(for: "D_HSBC_CSI300"))
        XCTAssertTrue(AssetCatalog.legacy.contains { $0.key == "D_CN_QDII_GLOBAL" })
    }
}
