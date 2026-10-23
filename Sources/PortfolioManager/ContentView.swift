import SwiftUI

/// 底部三大模块 + 最左侧「特别关注」（第 4 个模块，2026-10-23）。
/// 顺序即 Tab 顺序：特别关注在最前，首次启动默认落在它。
enum MainTab: String, Hashable { case focus, overview, perspective, analysis }

/// App shell: 3 tabs (资产总览 / 资产明细 / 财务分析) + bottom-right 运行/设置 pill.
struct ContentView: View {
    @State private var store: AppStore? = nil
    /// 记住上次停留的标签页 (重新打开时回到原处)。
    /// 默认值 = 特别关注：新装用户第一次打开看到的就是它；老用户已存过的值不受影响。
    @AppStorage("main.selectedTab") private var selectedTab: MainTab = .focus

    var body: some View {
        Group {
            if let store {
                mainTabs(store)
            } else {
                ProgressView("正在初始化…")
                    .frame(minWidth: 900, minHeight: 600)
            }
        }
        .task {
            if store == nil {
                let s = AppStore.makeDefault()
                store = s
                s?.loadAll()
                // 能力1/能力2: 启动时自动抓取有效汇率 + 行情 (异步, 不阻塞 UI)
                await s?.startupRefresh()
            }
        }
    }

    private func mainTabs(_ store: AppStore) -> some View {
        TabView(selection: $selectedTab) {
            FocusView(store: store)
                .tabItem { Label("特别关注", systemImage: "star.circle") }
                .tag(MainTab.focus)
            AssetOverviewView(store: store)
                .tabItem { Label("资产总览", systemImage: "chart.pie") }
                .tag(MainTab.overview)
            AssetPerspectiveView(store: store)
                .tabItem { Label("资产明细", systemImage: "list.bullet") }
                .tag(MainTab.perspective)
            FinancialAnalysisView(store: store)
                .tabItem { Label("财务分析", systemImage: "chart.pie.badge.percent") }
                .tag(MainTab.analysis)
        }
        .frame(minWidth: 900, minHeight: 600)
        // 「特别关注」点小图 → 切到资产明细；真正的选中由 AssetPerspectiveView 消费。
        .onChange(of: store.pendingAssetSelection) { _, pending in
            if pending != nil { selectedTab = .perspective }
        }
        // 导入备份确认面板: 「导入备份」是三大模块共用的全局工具条按钮,
        // 因此面板在窗口层挂载一次 (state 在 store 上), 而不是各模块各挂一份.
        .sheet(isPresented: Binding(
            get: { store.pendingImportURL != nil },
            set: { if !$0 { store.pendingImportURL = nil } }
        )) {
            ImportOptionsSheet(store: store)
        }
        .overlay(alignment: .bottomTrailing) {
            Group {
                if selectedTab == .overview {
                    OptimizeButton(store: store)
                } else {
                    SaveButton(store: store)
                }
            }
            .padding(20)
        }
    }
}
