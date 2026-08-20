import SwiftUI

/// 能力2：右下角 "运行/设置" 双功能胶囊按钮。运行优化时变成进度条并逐步展示日志。
public struct OptimizeButton: View {
    @Bindable var store: AppStore
    @State private var showSettings = false
    @State private var showLog = false
    @State private var showResult = false

    public var body: some View {
        Group {
            if store.isOptimizing {
                progressPill()
            } else {
                idlePill()
            }
        }
        .popover(isPresented: $showSettings) {
            settingsPopover()
        }
        .sheet(isPresented: $showResult) {
            if let r = store.lastOptimization {
                OptimizationResultView(result: r)
            }
        }
        .onChange(of: store.isOptimizing) { old, new in
            if old && !new && store.lastOptimization != nil && store.optimizeError == nil {
                showResult = true
            }
        }
    }

    // MARK: idle — 运行 / 设置 dual pill

    private func idlePill() -> some View {
        HStack(spacing: 0) {
            Button {
                store.runOptimization()
            } label: {
                Label("运行", systemImage: "play.fill")
                    .padding(.horizontal, 16).padding(.vertical, 10)
            }
            .buttonStyle(.plain)

            Divider().frame(height: 20)

            Button {
                showSettings = true
            } label: {
                Image(systemName: "gearshape")
                    .padding(.horizontal, 12).padding(.vertical, 10)
            }
            .buttonStyle(.plain)
        }
        .background(.ultraThinMaterial, in: Capsule())
        .overlay(Capsule().strokeBorder(.separator))
        .shadow(color: .black.opacity(0.15), radius: 6, y: 2)
        .help("运行投资组合优化 / 设置参数")
    }

    // MARK: running — progress pill

    private func progressPill() -> some View {
        HStack(spacing: 12) {
            ProgressView().controlSize(.small)
            VStack(alignment: .leading, spacing: 2) {
                Text(stepTitle()).font(.subheadline).fontWeight(.medium).lineLimit(1)
                Text("第 \(store.optimizeSteps.count) 步").font(.caption2).foregroundStyle(.secondary)
            }
            Button {
                showLog.toggle()
            } label: {
                Image(systemName: "list.bullet.rectangle").foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 16).padding(.vertical, 10)
        .background(.ultraThinMaterial, in: Capsule())
        .overlay(Capsule().strokeBorder(.separator))
        .shadow(color: .black.opacity(0.15), radius: 6, y: 2)
        .popover(isPresented: $showLog) {
            stepLogPopover()
        }
    }

    private func stepTitle() -> String {
        guard let last = store.optimizeSteps.last else { return "正在启动优化器…" }
        return "[" + last.step + "] " + last.message
    }

    private func stepLogPopover() -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("优化步骤日志").font(.headline).padding(.bottom, 4)
            ScrollView {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(Array(store.optimizeSteps.enumerated()), id: \.offset) { idx, s in
                        HStack(alignment: .top, spacing: 8) {
                            Text(String(idx + 1)).font(.caption).foregroundStyle(.secondary)
                                .frame(width: 20, alignment: .trailing)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(s.step).font(.caption).fontWeight(.semibold)
                                Text(s.message).font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }
            .frame(width: 360, height: 320)
        }
        .padding()
    }

    // MARK: settings

    private func settingsPopover() -> some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("优化设置").font(.headline)
            HStack {
                Text("目标收益率")
                Spacer()
                Text(String(format: "%.0f%%", store.targetReturn * 100))
                    .monospacedDigit().foregroundStyle(.secondary)
            }
            Slider(value: $store.targetReturn, in: 0.05...0.20, step: 0.01)
            Text("约束：境内池未达 50 万前只买汇丰中国开放申购基金；黄金与大中华权益可跨池分配。")
                .font(.caption).foregroundStyle(.secondary)
            Divider()
            HStack {
                Spacer()
                Button("关闭") { showSettings = false }.keyboardShortcut(.cancelAction)
            }
        }
        .padding()
        .frame(width: 320)
    }
}
