import Foundation

/// 资产明细的「编辑持仓」草稿集：唯一改动来源，视图只经 store 的 binding 写入。
///
/// 抽成 Core 的纯结构体（无 SwiftUI、无 I/O）：脏判定与字段规范以前写在
/// `AppStore.hasUnsavedChanges` 里，只能在真机上肉眼验证（ADR 0011）。
public struct HoldingEditor: Sendable {

    public private(set) var drafts: [String: HoldingDraft]

    public init(drafts: [String: HoldingDraft] = [:]) {
        self.drafts = drafts
    }

    /// 用当前行重建草稿（`loadAll()` 时调用）。
    public mutating func seed(_ rows: [AssetPerspectiveRow]) {
        drafts = Dictionary(uniqueKeysWithValues: rows.map {
            ($0.assetKey, HoldingDraft(quantity: $0.quantity,
                                       costBasis: $0.costBasis,
                                       currency: $0.currency))
        })
    }

    public func draft(_ key: String) -> HoldingDraft {
        drafts[key] ?? HoldingDraft()
    }

    /// 原样恢复草稿（拖动排序后 `loadAll()` 会重播种，不能把未保存的编辑冲掉）。
    public mutating func restore(_ drafts: [String: HoldingDraft]) {
        self.drafts = drafts
    }

    public mutating func setQuantity(_ key: String, _ value: Double) {
        var d = draft(key)
        d.quantity = value
        drafts[key] = d
    }

    public mutating func setCostBasis(_ key: String, _ value: Double) {
        var d = draft(key)
        d.costBasis = value
        drafts[key] = d
    }

    public mutating func setCurrency(_ key: String, _ value: String) {
        var d = draft(key)
        d.currency = value
        drafts[key] = d
    }

    /// 是否有未保存的修改：以当前行逐字段比较。
    /// 行已不存在的草稿不计（删除标的时不该留下「未保存」的假象）。
    public func isDirty(against rows: [AssetPerspectiveRow]) -> Bool {
        let byKey = Dictionary(uniqueKeysWithValues: rows.map { ($0.assetKey, $0) })
        for (key, draft) in drafts {
            guard let row = byKey[key] else { continue }
            if draft.quantity != row.quantity
                || draft.costBasis != row.costBasis
                || draft.currency != row.currency {
                return true
            }
        }
        return false
    }
}
