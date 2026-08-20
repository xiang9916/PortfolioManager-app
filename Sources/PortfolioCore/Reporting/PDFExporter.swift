import Foundation
import AppKit
import CoreGraphics
import CoreText

/// Capability 3: PDF export of the portfolio report (allocation + performance + asset detail).
/// Uses CoreGraphics PDF context + CoreText for selectable-text output (no external dependency).
public enum PDFError: Error, CustomStringConvertible {
    case context
    public var description: String { "cannot create PDF context" }
}

public final class PDFExporter {

    // A4 portrait in points.
    private static let pageWidth: CGFloat = 595
    private static let pageHeight: CGFloat = 842
    private static let margin: CGFloat = 48

    public static func writeReport(
        to url: URL,
        allocation: AllocationSnapshot,
        performance: PerformanceSummary,
        rows: [AssetPerspectiveRow],
        generatedAt: String
    ) throws {
        var mediaBox = CGRect(x: 0, y: 0, width: pageWidth, height: pageHeight)
        guard let ctx = CGContext(url as CFURL, mediaBox: &mediaBox, nil) else {
            throw PDFError.context
        }
        ctx.beginPDFPage(nil)   // a PDF context does not auto-begin its first page

        var y = pageHeight - margin   // cursor, top of current line block
        let contentWidth = pageWidth - margin * 2
        let left = margin

        func beginPageIfNeeded(_ height: CGFloat) {
            if y - height < margin {
                ctx.beginPDFPage(nil)
                y = pageHeight - margin
            }
        }
        func emit(_ text: NSAttributedString, height: CGFloat) {
            beginPageIfNeeded(height)
            draw(text, ctx: ctx, x: left, topY: y, width: contentWidth)
            y -= height
        }

        let body = font(.systemFont(ofSize: 11))
        let title = font(.boldSystemFont(ofSize: 22))
        let h2 = font(.boldSystemFont(ofSize: 14))

        // Title
        emit(attr("投资组合报告", title), height: 30)
        emit(attr("生成时间: " + generatedAt, body), height: 16)
        emit(attr(" ", body), height: 10)

        // Summary
        emit(attr("一、组合概览", h2), height: 22)
        let money = NumberFormatter()
        money.numberStyle = .decimal
        money.maximumFractionDigits = 0
        func fmtMoney(_ v: Double) -> String {
            return "¥" + (money.string(from: NSNumber(value: v)) ?? "0")
        }
        func pct(_ v: Double) -> String {
            return String(format: "%.2f%%", v * 100)
        }
        var summaryLines: [String] = []
        summaryLines.append("总资产: " + fmtMoney(allocation.totalValue))
        summaryLines.append("境内资产: " + fmtMoney(allocation.domesticValue) + "    境外资产: " + fmtMoney(allocation.overseasValue))
        summaryLines.append("累计收益: " + pct(performance.totalReturn))
        summaryLines.append("年化波动率: " + pct(performance.annualizedVolatility))
        summaryLines.append("最大回撤: " + pct(performance.maxDrawdown))
        var rangeText = "数据点: " + String(performance.pointCount)
        if let sd = performance.startDate {
            rangeText += "   区间: " + sd + " ~ " + (performance.endDate ?? "")
        }
        summaryLines.append(rangeText)
        for line in summaryLines { emit(attr(line, body), height: 16) }
        emit(attr(" ", body), height: 10)

        // Allocation table
        emit(attr("二、资产大类配置", h2), height: 22)
        var table: [[String]] = [["资产类别", "市值(¥)", "占比", "池"]]
        for s in allocation.slices {
            table.append([label(s.assetClass), fmtMoney(s.value), pct(s.weight), poolLabel(s.pool)])
        }
        emitTable(table, colWeights: [0.40, 0.30, 0.16, 0.14], ctx: ctx, topY: &y, left: left, contentWidth: contentWidth)
        emit(attr(" ", body), height: 12)

        // Asset detail table
        emit(attr("三、资产明细", h2), height: 22)
        var detail: [[String]] = [["标的", "类别", "市值(¥)", "占比", "最新价", "最新日期"]]
        for r in rows {
            detail.append([
                r.name + " (" + r.assetKey + ")",
                label(r.assetClass),
                fmtMoney(r.valueCny),
                pct(r.weight),
                r.latestPrice.map { String(format: "%.4f", $0) } ?? "—",
                r.latestDate ?? "—",
            ])
        }
        emitTable(detail, colWeights: [0.32, 0.16, 0.16, 0.10, 0.12, 0.14], ctx: ctx, topY: &y, left: left, contentWidth: contentWidth)

        ctx.closePDF()
    }

    // MARK: - helpers

    private static func font(_ f: NSFont) -> NSFont { f }
    private static func attr(_ s: String, _ f: NSFont) -> NSAttributedString {
        NSAttributedString(string: s, attributes: [.font: f])
    }

    private static func label(_ cls: String) -> String {
        let map = [
            "us_equity": "美国核心权益", "cn_fixed_income": "境内固收", "us_fixed_income": "美国固收",
            "greater_cn_equity": "大中华权益", "us_reit": "美国REIT", "btc": "比特币", "gold": "黄金",
            "jp_equity": "日本权益", "sg_equity": "新加坡权益", "energy": "能源", "other": "其他",
        ]
        return map[cls] ?? cls
    }
    private static func poolLabel(_ p: Pool) -> String {
        switch p { case .domestic: return "境内"; case .overseas: return "境外"; case .cross: return "跨池" }
    }

    private static func draw(_ text: NSAttributedString, ctx: CGContext, x: CGFloat, topY: CGFloat, width: CGFloat) {
        let framesetter = CTFramesetterCreateWithAttributedString(text as CFAttributedString)
        let path = CGPath(rect: CGRect(x: x, y: topY - 2000, width: width, height: 4000), transform: nil)
        let frame = CTFramesetterCreateFrame(framesetter, CFRange(location: 0, length: 0), path, nil)
        ctx.saveGState()
        ctx.textMatrix = .identity
        ctx.translateBy(x: 0, y: topY)
        ctx.scaleBy(x: 1, y: -1)
        CTFrameDraw(frame, ctx)
        ctx.restoreGState()
    }

    private static func measure(_ text: NSAttributedString, width: CGFloat) -> CGFloat {
        let framesetter = CTFramesetterCreateWithAttributedString(text as CFAttributedString)
        let size = CTFramesetterSuggestFrameSizeWithConstraints(
            framesetter, CFRange(location: 0, length: 0), nil,
            CGSize(width: width, height: .greatestFiniteMagnitude), nil)
        return ceil(size.height)
    }

    private static func emitTable(_ rows: [[String]], colWeights: [CGFloat], ctx: CGContext,
                                  topY: inout CGFloat, left: CGFloat, contentWidth: CGFloat) {
        let body = font(.systemFont(ofSize: 10))
        let bold = font(.boldSystemFont(ofSize: 10))
        let rowHeight: CGFloat = 15
        let cols = colWeights.count
        let widths = colWeights.map { $0 * contentWidth }
        // column x positions
        var xs: [CGFloat] = [left]
        for i in 1..<cols { xs.append(xs[i - 1] + widths[i - 1]) }

        // header background
        let headerRect = CGRect(x: left, y: topY - rowHeight - 2, width: contentWidth, height: rowHeight + 2)
        ctx.saveGState()
        ctx.setFillColor(CGColor(gray: 0.88, alpha: 1))
        ctx.fill(headerRect)
        ctx.restoreGState()

        for (ri, row) in rows.enumerated() {
            if topY - rowHeight < margin { ctx.beginPDFPage(nil); topY = pageHeight - margin }
            if ri % 2 == 1 {
                let rr = CGRect(x: left, y: topY - rowHeight, width: contentWidth, height: rowHeight)
                ctx.saveGState()
                ctx.setFillColor(CGColor(gray: 0.96, alpha: 1))
                ctx.fill(rr)
                ctx.restoreGState()
            }
            for (ci, cell) in row.enumerated() {
                let f = ri == 0 ? bold : body
                let a = NSAttributedString(string: cell, attributes: [.font: f])
                // right-align numeric columns (index 1..)
                let w = widths[ci]
                let textWidth = min(measure(a, width: w), w)
                let x = ci == 0 || ri == 0 ? xs[ci] : xs[ci] + (w - textWidth)
                draw(a, ctx: ctx, x: x, topY: topY - 2, width: w)
            }
            topY -= rowHeight
        }
    }
}
