import SwiftUI
import PortfolioCore

/// Shared date formatters — creating a DateFormatter on every call is expensive
/// (format string parsing + timezone lookup). Reuse static instances.
public enum DateFormatters {
    /// UTC "yyyy-MM-dd" — used for DB dates, performance series, fund codes.
    public static let utcDay: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.timeZone = TimeZone(identifier: "UTC")
        return f
    }()

    /// Today's date in UTC (convenience).
    public static func todayUTC() -> String {
        utcDay.string(from: Date())
    }

    /// Current ISO8601 timestamp (creates a new formatter each call — ISO8601DateFormatter
    /// is not Sendable so cannot be a static let under strict concurrency).
    public static func nowISO() -> String {
        ISO8601DateFormatter().string(from: Date())
    }
}

/// Shared UI styling: asset-class display names + deterministic colors + date helpers.
public enum AssetClassStyle {
    public static func displayName(_ cls: String) -> String {
        switch cls {
        case "us_equity": return "美国核心权益"
        case "cn_fixed_income": return "境内固收"
        case "us_fixed_income": return "美国固收"
        case "greater_cn_equity": return "大中华权益"
        case "us_reit": return "美国REIT"
        case "btc": return "比特币"
        case "gold": return "黄金"
        case "jp_equity": return "日本权益"
        case "sg_equity": return "新加坡权益"
        case "energy": return "能源"
        case "other": return "其他"
        default: return cls
        }
    }

    public static func poolName(_ p: Pool) -> String {
        switch p {
        case .domestic: return "境内"
        case .overseas: return "境外"
        case .cross: return "跨池"
        }
    }

    public static func color(_ cls: String) -> Color {
        switch cls {
        case "us_equity": return .blue
        case "cn_fixed_income": return .teal
        case "us_fixed_income": return .cyan
        case "greater_cn_equity": return .red
        case "us_reit": return .purple
        case "btc": return .orange
        case "gold": return .yellow
        case "jp_equity": return .pink
        case "sg_equity": return .indigo
        case "energy": return .brown
        default: return .gray
        }
    }

    public static func parseDate(_ s: String) -> Date {
        DateFormatters.utcDay.date(from: s) ?? .distantPast
    }
}

/// Currency symbol helper (币种显示).
public enum CurrencyStyle {
    public static func symbol(_ code: String) -> String {
        switch code {
        case "USD": return "$"
        case "HKD": return "HK$"
        case "JPY": return "¥"
        case "CNY": return "¥"
        case "SGD": return "S$"
        case "EUR": return "€"
        case "GBP": return "£"
        case "AUD": return "A$"
        case "CAD": return "C$"
        case "CHF": return "CHF "
        default: return code + " "
        }
    }
}

/// macOS 原生"红绿灯"风格的关闭按钮：12pt 红色圆点，悬停时显示 ×。
/// 用于弹窗标题左侧，模拟原生窗口标题栏的关闭按钮。
public struct MacCloseButton: View {
    var action: () -> Void
    @State private var isHovering = false

    public init(action: @escaping () -> Void) {
        self.action = action
    }

    public var body: some View {
        Button(action: action) {
            ZStack {
                Circle()
                    .fill(
                        LinearGradient(
                            stops: [
                                .init(color: Color(red: 1.00, green: 0.42, blue: 0.38), location: 0.0),
                                .init(color: Color(red: 0.96, green: 0.30, blue: 0.26), location: 0.5),
                                .init(color: Color(red: 0.88, green: 0.23, blue: 0.20), location: 1.0),
                            ],
                            startPoint: .top, endPoint: .bottom
                        )
                    )
                Circle()
                    .strokeBorder(
                        Color(red: 0.80, green: 0.13, blue: 0.10)
                            .opacity(isHovering ? 0.85 : 0.55),
                        lineWidth: 0.8
                    )
                Image(systemName: "xmark")
                    .font(.system(size: 7.5, weight: .bold))
                    .foregroundStyle(Color(red: 0.35, green: 0.0, blue: 0.0).opacity(0.8))
                    .opacity(isHovering ? 1 : 0)
            }
            .frame(width: 12, height: 12)
            .padding(4) // 扩大点击热区
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .keyboardShortcut(.cancelAction)
        .onHover { isHovering = $0 }
        .help("关闭")
    }
}

/// A chart-friendly plottable point (Date x-axis).
/// id carries a series tag: 同一张图里多条系列的 ForEach 不能复用相同的 id
/// (SwiftUI 会按 id 合并/丢 mark), 组合/基准各用独立前缀.
public struct ChartPoint: Identifiable {
    public let date: Date
    public let value: Double
    public let id: String

    public init(date: Date, value: Double, id: String) {
        self.date = date
        self.value = value
        self.id = id
    }
}

public extension Array where Element == PerformancePoint {
    func chartPoints(series: String = "p") -> [ChartPoint] {
        map { ChartPoint(date: AssetClassStyle.parseDate($0.date), value: $0.value, id: series + ":" + $0.date) }
    }
}
