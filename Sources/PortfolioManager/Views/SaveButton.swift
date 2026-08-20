import SwiftUI

/// 右下角「保存」胶囊按钮：持久化资产透视的持仓编辑。
struct SaveButton: View {
    @Bindable var store: AppStore

    var body: some View {
        HStack(spacing: 8) {
            if store.hasUnsavedChanges {
                Circle().fill(.orange).frame(width: 7, height: 7)
            }
            Button {
                store.savePerspectives()
            } label: {
                Label("保存", systemImage: store.hasUnsavedChanges ? "square.and.arrow.down" : "checkmark.circle")
                    .padding(.horizontal, 16).padding(.vertical, 10)
            }
            .buttonStyle(.plain)
        }
        .background(.ultraThinMaterial, in: Capsule())
        .overlay(Capsule().strokeBorder(.separator))
        .shadow(color: .black.opacity(0.15), radius: 6, y: 2)
        .help("保存资产透视的持仓编辑")
    }
}
