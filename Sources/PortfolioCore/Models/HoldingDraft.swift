import Foundation

/// 「编辑持仓」的草稿（份额 / 成本 / 币种）。
///
/// 原本定义在 App 层 `AppStore.swift` 顶部；移到 Core 是为了让 `HoldingEditor` 的
/// 脏判定能被单测锁住（App 层没有 testTarget，ADR 0011）。
public struct HoldingDraft: Hashable, Sendable {
    public var quantity: Double
    public var costBasis: Double
    public var currency: String

    public init(quantity: Double = 0, costBasis: Double = 0, currency: String = "CNY") {
        self.quantity = quantity
        self.costBasis = costBasis
        self.currency = currency
    }
}
