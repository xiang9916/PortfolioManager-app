import SwiftUI

/// App shell: 3 tabs (资产管理 / 资产透视 / 财务报表) + bottom-right 运行/设置 pill.
struct ContentView: View {
    @State private var store: AppStore? = nil

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
        TabView {
            AssetOverviewView(store: store)
                .tabItem { Label("资产管理", systemImage: "chart.pie") }
            AssetPerspectiveView(store: store)
                .tabItem { Label("资产透视", systemImage: "list.bullet") }
            FinancialComparisonView(store: store)
                .tabItem { Label("财务报表", systemImage: "chart.bar.doc.horizontal") }
        }
        .frame(minWidth: 900, minHeight: 600)
        .overlay(alignment: .bottomTrailing) {
            OptimizeButton(store: store)
                .padding(20)
        }
    }
}
