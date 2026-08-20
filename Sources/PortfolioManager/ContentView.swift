import SwiftUI

struct ContentView: View {
    var body: some View {
        TabView {
            Text("资产管理 (模块1) — 待实现").tabItem { Label("资产管理", systemImage: "chart.pie") }
            Text("资产透视 (模块2) — 待实现").tabItem { Label("资产透视", systemImage: "list.bullet") }
        }
        .frame(minWidth: 900, minHeight: 600)
    }
}
