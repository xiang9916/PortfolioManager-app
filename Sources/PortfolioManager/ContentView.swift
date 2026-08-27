import SwiftUI

enum MainTab: String, Hashable { case overview, perspective, analysis }

/// App shell: 3 tabs (资产管理 / 资产透视 / 财务报表) + bottom-right 运行/设置 pill.
struct ContentView: View {
    @State private var store: AppStore? = nil
    /// 记住上次停留的标签页 (重新打开时回到原处).
    @AppStorage("main.selectedTab") private var selectedTab: MainTab = .overview

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
            AssetOverviewView(store: store)
                .tabItem { Label("资产管理", systemImage: "chart.pie") }
                .tag(MainTab.overview)
            AssetPerspectiveView(store: store)
                .tabItem { Label("资产透视", systemImage: "list.bullet") }
                .tag(MainTab.perspective)
            FinancialAnalysisView(store: store)
                .tabItem { Label("财务分析", systemImage: "chart.pie.badge.percent") }
                .tag(MainTab.analysis)
        }
        .frame(minWidth: 900, minHeight: 600)
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
