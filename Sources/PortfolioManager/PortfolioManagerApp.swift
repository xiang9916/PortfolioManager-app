import SwiftUI

// SwiftUI entry point. Compiled as part of the package; a full .app bundle needs
// complete Xcode (xcodebuild) — see docs/specs for packaging notes.
@main
struct PortfolioManagerApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
        }
    }
}
