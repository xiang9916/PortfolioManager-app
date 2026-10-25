import XCTest
@testable import PortfolioCore

/// `HoldingEditor`：草稿写入与脏判定（以前写在 App 层，无法单测）。
final class HoldingEditorTests: XCTestCase {

    func testSeedFromRowsIsNotDirty() {
        var editor = HoldingEditor()
        editor.seed([row("A", quantity: 10, cost: 100, currency: "USD")])
        XCTAssertEqual(editor.drafts["A"], HoldingDraft(quantity: 10, costBasis: 100, currency: "USD"))
        XCTAssertFalse(editor.isDirty(against: [row("A", quantity: 10, cost: 100, currency: "USD")]))
    }

    func testQuantityChangeMarksDirty() {
        var editor = HoldingEditor()
        let rows = [row("A", quantity: 10, cost: 100, currency: "USD")]
        editor.seed(rows)
        editor.setQuantity("A", 11)
        XCTAssertTrue(editor.isDirty(against: rows))
    }

    func testCostChangeMarksDirty() {
        var editor = HoldingEditor()
        let rows = [row("A", quantity: 10, cost: 100, currency: "USD")]
        editor.seed(rows)
        editor.setCostBasis("A", 101)
        XCTAssertTrue(editor.isDirty(against: rows))
    }

    func testCurrencyChangeAndRevert() {
        var editor = HoldingEditor()
        let rows = [row("A", quantity: 10, cost: 100, currency: "USD")]
        editor.seed(rows)
        editor.setCurrency("A", "HKD")
        XCTAssertTrue(editor.isDirty(against: rows))
        editor.setCurrency("A", "USD")
        XCTAssertFalse(editor.isDirty(against: rows), "改回原值后不再算脏")
    }

    func testDraftForMissingRowIsIgnored() {
        var editor = HoldingEditor()
        let rows = [row("A", quantity: 10, cost: 100, currency: "USD")]
        editor.seed(rows)
        editor.setQuantity("GHOST", 5)
        XCTAssertFalse(editor.isDirty(against: rows), "行已不存在的草稿不计")
    }

    func testSettingOnUnknownKeyCreatesDefaultDraft() {
        var editor = HoldingEditor()
        editor.setQuantity("NEW", 3)
        XCTAssertEqual(editor.draft("NEW"), HoldingDraft(quantity: 3, costBasis: 0, currency: "CNY"))
        XCTAssertEqual(editor.draft("MISSING"), HoldingDraft())
    }

    private func row(_ key: String, quantity: Double, cost: Double,
                     currency: String) -> AssetPerspectiveRow {
        AssetPerspectiveRow(assetKey: key, name: "name-" + key, assetClass: "us_equity",
                            market: .us, pool: .overseas, currency: currency,
                            value: 1, valueCny: 1, costCny: cost, unrealizedPnl: 0,
                            returnRate: 0, quantity: quantity, costBasis: cost, weight: 0,
                            latestPrice: nil, latestDate: nil, sortOrder: 0, dividend: nil)
    }
}
