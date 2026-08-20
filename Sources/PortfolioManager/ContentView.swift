import SwiftUI

enum MainTab: Hashable { case overview, perspective, financial }

/// App shell: 3 tabs (资产管理 / 资产透视 / 财务报表) + bottom-right 运行/设置 pill.
struct ContentView: View {
    @State private var store: AppStore? = nil
    @State private var selectedTab: MainTab = .overview

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
                store = AppStore.makeDefault()
                store?.loadAll()
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
            FinancialComparisonView(store: store)
                .tabItem { Label("财务报表", systemImage: "chart.bar.doc.horizontal") }
                .tag(MainTab.financial)
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
