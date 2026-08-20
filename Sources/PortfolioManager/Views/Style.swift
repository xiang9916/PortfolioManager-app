import SwiftUI
import PortfolioCore

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

    private static let dateFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.timeZone = TimeZone(identifier: "UTC")
        return f
    }()

    public static func parseDate(_ s: String) -> Date {
        dateFormatter.date(from: s) ?? .distantPast
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

/// A chart-friendly plottable point (Date x-axis).
public struct ChartPoint: Identifiable {
    public let date: Date
    public let value: Double
    public var id: Date { date }
}

public extension Array where Element == PerformancePoint {
    var chartPoints: [ChartPoint] {
        map { ChartPoint(date: AssetClassStyle.parseDate($0.date), value: $0.value) }
    }
}
