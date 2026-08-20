// Generates a 1024x1024 app icon PNG: rounded-square gradient + white ascending chart.
import Foundation
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers

let size = 1024
let sRGB = CGColorSpace(name: CGColorSpace.sRGB)!
guard let ctx = CGContext(data: nil, width: size, height: size, bitsPerComponent: 8,
                          bytesPerRow: 0, space: sRGB,
                          bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
    fatalError("no context")
}

// macOS icon grid: draw inside a rounded rect with margins.
let margin = CGFloat(size) * 0.09
let rect = CGRect(x: margin, y: margin, width: CGFloat(size) - 2*margin, height: CGFloat(size) - 2*margin)
let radius = rect.width * 0.22
let path = CGPath(roundedRect: rect, cornerWidth: radius, cornerHeight: radius, transform: nil)
ctx.addPath(path)
ctx.clip()

// Vertical gradient background.
let colors = [CGColor(srgbRed: 0.10, green: 0.36, blue: 0.85, alpha: 1),
              CGColor(srgbRed: 0.05, green: 0.16, blue: 0.50, alpha: 1)] as CFArray
let grad = CGGradient(colorsSpace: sRGB, colors: colors, locations: [0, 1])!
ctx.drawLinearGradient(grad, start: CGPoint(x: rect.midX, y: rect.maxY),
                       end: CGPoint(x: rect.midX, y: rect.minY), options: [])

// White ascending polyline chart (trend up = growth).
// CG bitmap origin is bottom-left (larger y = higher), so an upward trend uses
// increasing y left-to-right.
let pts: [CGPoint] = [
    CGPoint(x: rect.minX + rect.width*0.18, y: rect.minY + rect.height*0.26),
    CGPoint(x: rect.minX + rect.width*0.36, y: rect.minY + rect.height*0.44),
    CGPoint(x: rect.minX + rect.width*0.52, y: rect.minY + rect.height*0.36),
    CGPoint(x: rect.minX + rect.width*0.68, y: rect.minY + rect.height*0.56),
    CGPoint(x: rect.minX + rect.width*0.84, y: rect.minY + rect.height*0.68),
]
ctx.setStrokeColor(CGColor(srgbRed: 1, green: 1, blue: 1, alpha: 0.95))
ctx.setLineWidth(rect.width * 0.045)
ctx.setLineCap(.round)
ctx.setLineJoin(.round)
ctx.move(to: pts[0])
for p in pts.dropFirst() { ctx.addLine(to: p) }
ctx.strokePath()

// Filled area under the ascending line, subtle (down to the bottom edge).
let area = CGMutablePath()
area.move(to: CGPoint(x: pts[0].x, y: rect.minY))
for p in pts { area.addLine(to: p) }
area.addLine(to: CGPoint(x: pts.last!.x, y: rect.minY))
area.closeSubpath()
ctx.addPath(area)
ctx.setFillColor(CGColor(srgbRed: 1, green: 1, blue: 1, alpha: 0.18))
ctx.fillPath()

guard let img = ctx.makeImage() else { fatalError("no image") }
let out = URL(fileURLWithPath: CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "AppIcon.png")
let dest = CGImageDestinationCreateWithURL(out as CFURL, UTType.png.identifier as CFString, 1, nil)!
CGImageDestinationAddImage(dest, img, nil)
CGImageDestinationFinalize(dest)
print("wrote \(out.path)")
