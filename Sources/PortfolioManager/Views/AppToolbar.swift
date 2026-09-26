import SwiftUI
import AppKit
import UniformTypeIdentifiers
import PortfolioCore

// MARK: - 全局工具条（三大模块右上角共用）

/// 右上角「更新行情 ｜ 隐藏数字 ｜ 导出备份 ｜ 导入备份」四个全局按钮。
///
/// 用法：拼在任意模块 `.toolbar { }` 的最后一项即可；模块专属按钮放在前一个
/// `ToolbarItemGroup` 里，末尾接一个 `ToolbarDivider()` 就是那条「｜」。
/// 导出/导入的落盘逻辑与导入确认面板集中在这里，各模块不再各自实现一份。
struct GlobalToolbarContent: ToolbarContent {
    let store: AppStore

    var body: some ToolbarContent {
        ToolbarItemGroup(placement: .primaryAction) {
            Button {
                Task { await store.refreshPrices() }
            } label: {
                Label("更新行情", systemImage: "arrow.clockwise")
            }
            .help("自动抓取公开行情数据（能力1）")

            Button {
                store.hideNumbers.toggle()
            } label: {
                Label("隐藏数字", systemImage: store.hideNumbers ? "eye.slash" : "eye")
            }
            .help(store.hideNumbers ? "已隐藏数字，点击恢复显示" : "隐藏所有数字（隐私）")

            Button {
                presentExportPanel(store)
            } label: {
                Label("导出备份", systemImage: "square.and.arrow.up")
            }
            .help("导出备份数据 JSON（持仓/收益期间/汇率）")

            Button {
                presentImportPanel(store)
            } label: {
                Label("导入备份", systemImage: "square.and.arrow.down")
            }
            .help("导入备份数据 JSON")
        }
    }
}

/// 工具条分组竖线「｜」：把模块专属按钮与全局按钮分开。
struct ToolbarDivider: View {
    var body: some View {
        Rectangle()
            .fill(Color.secondary.opacity(0.35))
            .frame(width: 1, height: 16)
            .padding(.horizontal, 4)
            .accessibilityHidden(true)
    }
}

// MARK: - 导出 / 导入

@MainActor
private func presentExportPanel(_ store: AppStore) {
    let panel = NSSavePanel()
    panel.allowedContentTypes = [UTType.json]
    panel.nameFieldStringValue = "PortfolioBackup.json"
    guard panel.runModal() == .OK, let url = panel.url else { return }
    do {
        try store.exportBackup(to: url)
        store.statusMessage = "已导出备份: \(url.lastPathComponent)"
    } catch {
        store.statusMessage = "导出备份失败: \(error)"
    }
}

@MainActor
private func presentImportPanel(_ store: AppStore) {
    let panel = NSOpenPanel()
    panel.allowedContentTypes = [UTType.json]
    panel.allowsMultipleSelection = false
    guard panel.runModal() == .OK, let url = panel.url else { return }
    // 选中文件后先弹确认框：用户决定是否清空旧资产 / 旧财务数据再导入。
    store.pendingImportURL = url
}

/// 导入确认面板（勾选是否清空旧数据）。由 ContentView 统一挂载一次，三大模块共用。
struct ImportOptionsSheet: View {
    @Bindable var store: AppStore

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Label("导入备份", systemImage: "square.and.arrow.down")
                .font(.headline)
            Text("备份文件: \(store.pendingImportURL?.lastPathComponent ?? "")")
                .font(.subheadline)
                .foregroundStyle(.secondary)
            Divider()
            Toggle("清空旧的资产数据", isOn: $store.clearAssetsOnImport)
                .help("持仓 / 历史价格 / 报价 / 市值快照 / 标的 / 汇率将先被清空，再写入备份内容")
            Toggle("清空旧的财务数据", isOn: $store.clearFinancialsOnImport)
                .help("财务分析逐季度底稿与收益期间将先被清空，再写入备份内容")
            Text("勾选对应项 = 先清空本机该类数据，使导入后与备份完全一致；\n不勾选 = 只合并备份中的条目，保留本机其余数据。")
                .font(.caption)
                .foregroundStyle(.tertiary)
            HStack {
                Spacer()
                Button("取消") { store.pendingImportURL = nil }
                    .keyboardShortcut(.cancelAction)
                Button("导入") { store.performImportBackup() }
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(20)
        .frame(width: 420)
    }
}
