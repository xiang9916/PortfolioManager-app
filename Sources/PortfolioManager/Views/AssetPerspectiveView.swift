import SwiftUI
import PortfolioCore

/// 模块2：资产明细 — 单个资产状态 + 数据更新 + 标的增删。
public struct AssetPerspectiveView: View {
    @Bindable var store: AppStore
    @State private var selectedKey: String?
    @State private var showAddAsset = false
    @State private var showFxRates = false

    public var body: some View {
        NavigationSplitView {
            List(selection: $selectedKey) {
                ForEach(store.perspectives) { row in
                    rowCell(row).tag(row.assetKey)
                }
                .onMove { source, destination in
                    store.moveAsset(from: source, to: destination)
                }
            }
            .navigationTitle("资产明细")
            .help("列表行可直接拖动调整顺序，排序自动保存")
        } detail: {
            Group {
                if let key = selectedKey, let row = store.perspectives.first(where: { $0.assetKey == key }) {
                    AssetDetailView(row: row, store: store)
                } else {
                    ContentUnavailableView("选择一项资产", systemImage: "list.bullet.rectangle")
                }
            }
            // 右上角: 模块专属按钮 ｜ 全局按钮.
            // 工具栏挂在 detail 上 (而不是侧边栏 List), 这样左上角只留侧边栏按钮.
            .toolbar {
                ToolbarItemGroup(placement: .primaryAction) {
                    Button { showAddAsset = true } label: { Label("添加标的", systemImage: "plus") }
                        .help("新增一个标的")
                    Button { showFxRates = true } label: { Label("汇率", systemImage: "dollarsign.arrow.circlepath") }
                        .help("查看 / 编辑各币种兑人民币汇率")
                    ToolbarDivider()
                }
                GlobalToolbarContent(store: store)
            }
        }
        .sheet(isPresented: $showAddAsset) { AddAssetSheet(store: store) }
        .sheet(isPresented: $showFxRates) { FxRatesSheet(store: store) }
    }

    private func rowCell(_ row: AssetPerspectiveRow) -> some View {
        HStack {
            Circle().fill(AssetClassStyle.color(row.assetClass)).frame(width: 10, height: 10)
            VStack(alignment: .leading, spacing: 2) {
                Text(row.name).font(.body)
                Text(row.assetKey + " · " + AssetClassStyle.displayName(row.assetClass))
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 2) {
                Text(store.hideNumbers ? PrivacyStyle.masked : CurrencyStyle.symbol(row.currency) + money(row.value))
                    .font(.subheadline).monospacedDigit()
                Text(store.hideNumbers ? PrivacyStyle.masked : pct(row.weight))
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 2)
    }

    private func money(_ v: Double) -> String {
        let f = NumberFormatter(); f.numberStyle = .decimal; f.maximumFractionDigits = 0
        return f.string(from: NSNumber(value: v)) ?? "0"
    }
    private func pct(_ v: Double) -> String { String(format: "%.2f%%", v * 100) }
}

/// Per-asset detail (模块2).
public struct AssetDetailView: View {
    let row: AssetPerspectiveRow
    @Bindable var store: AppStore
    @State private var confirmDelete = false

    public var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                HStack {
                    Circle().fill(AssetClassStyle.color(row.assetClass)).frame(width: 16, height: 16)
                    Text(row.name).font(.title2).fontWeight(.semibold)
                    Spacer()
                    Button(role: .destructive) { confirmDelete = true } label: {
                        Label("删除标的", systemImage: "trash")
                    }
                }
                detailRow("标的代码", row.assetKey)
                detailRow("资产类别", AssetClassStyle.displayName(row.assetClass))
                detailRow("市场", marketText(row.market))
                    .help(Text("市场是闭集（US/HK/CN/JP/SG），从标的代码推断或由你指定；「池」由它派生：中国内地 → 境内，其余 → 境外。"))
                detailRow("池", AssetClassStyle.poolName(row.pool))
                detailRow("币种", row.currency)
                detailRow("权重", store.hideNumbers ? PrivacyStyle.masked : pct(row.weight))
                if let p = row.latestPrice {
                    detailRow("最后价", store.hideNumbers ? PrivacyStyle.masked : String(format: "%.4f", p))
                }
                if let d = row.latestDate { detailRow("最后日期", d) }
                detailRow("市值 (" + row.currency + ")",
                          store.hideNumbers ? PrivacyStyle.masked : CurrencyStyle.symbol(row.currency) + money(row.value))
                detailRow("市值 (折人民币)", store.hideNumbers ? PrivacyStyle.masked : money(row.valueCny) + " ¥")
                detailRow("成本 (折人民币)", store.hideNumbers ? PrivacyStyle.masked : money(row.costCny) + " ¥")
                pnlRow("未实现资本利得", row.unrealizedPnl)
                detailRow("收益率", store.hideNumbers ? PrivacyStyle.masked : pct(row.returnRate))

                // 股息 (自动抓取, 不可手改): 四个指定字段 + 对照/依据行.
                if let d = row.dividend {
                    Divider()
                    dividendCurrencyRow(
                        "前年股息", amount: d.prevPrevTotal, d: d,
                        help: "\(d.yearPrevPrev) 年按除息日归入的每股股息合计 × 份额（\(d.currency)，税前）。")
                    dividendCurrencyRow(
                        "去年股息", amount: d.prevTotal, d: d,
                        help: "\(d.yearPrev) 年按除息日归入的每股股息合计 × 份额（\(d.currency)，税前）。")
                    dividendCurrencyRow(
                        "今年预计股息 (" + d.currency + ")", amount: d.estimatedTotal, d: d,
                        help: "= max(0, 2×去年每股股息 − 前年每股股息) × 份额（\(d.currency)，税前）；负值按 0 计。")
                    dividendCnyRow(
                        "今年预计股息 (折人民币)", value: d.estimatedTotalCny, d: d,
                        help: "= 今年预计股息（\(d.currency)，税前）× 汇率 × (1 − 股息税率)。")
                    dividendCnyRow(
                        "今年已发生股息 (折人民币)", value: d.actualYtdCny, d: d,
                        help: "\(d.yearCurrent) 年已除息的股息合计 × 份额 × 汇率 × (1 − 股息税率)，用于对照「今年预计」的偏差。")
                    detailRow("去年每股股息 (\(d.yearPrev), \(d.currency))", perShareText(d.perSharePrev, d: d))
                    detailRow("前年每股股息 (\(d.yearPrevPrev), \(d.currency))", perShareText(d.perSharePrevPrev, d: d))
                    detailRow("股息数据来源", dividendSourceText(d))
                }

                GroupBox("编辑持仓（改完点右下角「保存」）") {
                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            Text("币种").foregroundStyle(.secondary)
                            Spacer()
                            Picker("", selection: store.holdingCurrencyBinding(row.assetKey)) {
                                ForEach(AppStore.currencyOptions, id: \.self) { c in
                                    Text(c).tag(c)
                                }
                            }
                            .labelsHidden().frame(width: 120)
                        }
                        let ccy = store.holdingDrafts[row.assetKey]?.currency ?? row.currency
                        editableField("份额 / 数量", store.holdingBinding(row.assetKey, \.quantity))
                        editableField("成本 (" + ccy + ")", store.holdingBinding(row.assetKey, \.costBasis))
                        Text("市值 = 份额 × 最后价，自动抓取计算，无需手填").font(.caption).foregroundStyle(.secondary)
                    }
                    .padding(6)
                }
            }
            .padding()
        }
        .navigationTitle(row.name)
        .confirmationDialog("删除 \(row.name)？", isPresented: $confirmDelete, titleVisibility: .visible) {
            Button("删除标的", role: .destructive) { store.deleteAsset(key: row.assetKey) }
            Button("取消", role: .cancel) {}
        } message: {
            Text("将同时删除该标的的持仓、价格历史与财务报表，不可恢复。")
        }
    }

    private func editableField(_ label: String, _ value: Binding<Double>) -> some View {
        HStack {
            Text(label).foregroundStyle(.secondary)
            Spacer()
            if store.hideNumbers {
                Text(PrivacyStyle.masked)
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
                    .frame(width: 200, alignment: .trailing)
            } else {
                TextField("", value: value, format: .number)
                    .textFieldStyle(.roundedBorder)
                    .multilineTextAlignment(.trailing)
                    .monospacedDigit()
                    .frame(width: 200)
            }
        }
    }

    private func detailRow(_ label: String, _ value: String) -> some View {
        HStack { Text(label).foregroundStyle(.secondary); Spacer(); Text(value).monospacedDigit() }
        .padding(.vertical, 4)
    }

    // MARK: 股息 (只读, 自动抓取)

    /// 市场行 — nil = 未设置且从代码推断不出，池随即回退到币种口径。
    private func marketText(_ m: AssetMarket?) -> String {
        m?.displayLabel ?? "未设置（按币种回退）"
    }

    /// 原币金额行 — nil (数据源无覆盖 / 抓取失败) 显示「—」.
    private func dividendCurrencyRow(_ label: String, amount: Double?,
                                     d: AssetDividend, help: String) -> some View {
        let text: String
        if let amount {
            text = store.hideNumbers ? PrivacyStyle.masked
                                     : CurrencyStyle.symbol(d.currency) + amount2(amount)
        } else {
            text = "—"
        }
        return detailRow(label, text).help(Text(help))
    }

    /// 折人民币金额行 — 缺汇率时明确显示「缺汇率」而不是静默按 1.0 折算.
    private func dividendCnyRow(_ label: String, value: Double?,
                                d: AssetDividend, help: String) -> some View {
        let text: String
        if let value {
            text = store.hideNumbers ? PrivacyStyle.masked : amount2(value) + " ¥"
        } else if d.fxMissing {
            text = "缺汇率"
        } else {
            text = "—"
        }
        return detailRow(label, text).help(Text(help))
    }

    private func perShareText(_ v: Double, d: AssetDividend) -> String {
        guard d.status == .ok else { return "—" }
        if store.hideNumbers { return PrivacyStyle.masked }
        return CurrencyStyle.symbol(d.currency) + String(format: "%.4f", v)
    }

    private func dividendSourceText(_ d: AssetDividend) -> String {
        switch d.status {
        case .ok:
            var s = (d.source ?? "未知来源") + " · " + (d.fetchedAt ?? "—")
            s += " · 税率 " + DividendTax.label(d.taxRate)
            if d.fxMissing { s += " · 缺汇率" }
            return s
        case .unavailable:
            return "数据源无该标的历史（按 — 计, 汇总按 0）"
        case .failed:
            return "抓取失败（按 — 计, 汇总按 0）"
        }
    }

    private func amount2(_ v: Double) -> String {
        let f = NumberFormatter(); f.numberStyle = .decimal
        f.minimumFractionDigits = 2; f.maximumFractionDigits = 2
        return f.string(from: NSNumber(value: v)) ?? "0.00"
    }
    private func money(_ v: Double) -> String {
        let f = NumberFormatter(); f.numberStyle = .decimal; f.maximumFractionDigits = 0
        return f.string(from: NSNumber(value: v)) ?? "0"
    }
    private func signedMoney(_ v: Double) -> String {
        (v >= 0 ? "+" : "-") + money(abs(v))
    }
    private func pct(_ v: Double) -> String { String(format: "%.2f%%", v * 100) }

    private func pnlRow(_ label: String, _ v: Double) -> some View {
        HStack {
            Text(label).foregroundStyle(.secondary)
            Spacer()
            if store.hideNumbers {
                Text(PrivacyStyle.masked).monospacedDigit().foregroundStyle(.secondary)
            } else {
                Text(signedMoney(v) + " ¥").monospacedDigit()
                    .foregroundStyle(v >= 0 ? .green : .red)
            }
        }
        .padding(.vertical, 4)
    }
}