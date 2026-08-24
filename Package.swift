// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "PortfolioManager",
    platforms: [.macOS(.v14)],
    targets: [
        .target(name: "PortfolioCore"),
        .executableTarget(name: "pm-cli", dependencies: ["PortfolioCore"]),
        .executableTarget(name: "PortfolioManager", dependencies: ["PortfolioCore"]),
        .testTarget(name: "PortfolioCoreTests", dependencies: ["PortfolioCore"]),
    ]
)
