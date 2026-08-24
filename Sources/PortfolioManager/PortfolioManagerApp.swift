import SwiftUI
import AppKit
import PortfolioCore

// SwiftUI entry point. Compiled as part of the package; a full .app bundle needs
// complete Xcode (xcodebuild) — see docs/specs for packaging notes.
@main
struct PortfolioManagerApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
                .onReceive(NotificationCenter.default.publisher(
                    for: NSApplication.willTerminateNotification)) { _ in
                    // 关闭优化器: 删除联网管线临时文件 (基金搜索易/天天基金实时产物)
                    OptimizationService.cleanupWorkDir()
                }
        }
    }
}
