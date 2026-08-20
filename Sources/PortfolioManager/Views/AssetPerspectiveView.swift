import SwiftUI
import PortfolioCore

/// 模块2：资产透视 — 单个资产状态 + 数据更新 + 标的增删。
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
            }
            .navigationTitle("资产透视")
            .toolbar {
                ToolbarItemGroup {
                    Button { showAddAsset = true } label: { Label("添加标的", systemImage: "plus") }
                    Button { showFxRates = true } label: { Label("汇率", systemImage: "dollarsign.arrow.circlepath") }
                    Button { Task { await store.refreshPrices() } } label: { Label("数据更新", systemImage: "arrow.clockwise") }
                }
            }
        } detail: {
            if let key = selectedKey, let row = store.perspectives.first(where: { $0.assetKey == key }) {
                AssetDetailView(row: row, store: store)
            } else {
                ContentUnavailableView("选择一项资产", systemImage: "list.bullet.rectangle")
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
                Text(CurrencyStyle.symbol(row.currency) + money(row.value)).font(.subheadline).monospacedDigit()
                Text(pct(row.weight)).font(.caption).foregroundStyle(.secondary)
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
                detailRow("池", AssetClassStyle.poolName(row.pool))
                detailRow("币种", row.currency)
                detailRow("权重", pct(row.weight))
                detailRow("折合人民币", money(row.valueCny) + " ¥")
                if let p = row.latestPrice { detailRow("最新价", String(format: "%.4f", p)) }
                if let d = row.latestDate { detailRow("最新日期", d) }

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
                        editableField("市值 (" + ccy + ")", store.holdingBinding(row.assetKey, \.value))
                        editableField("份额 / 数量", store.holdingBinding(row.assetKey, \.quantity))
                        editableField("成本 (" + ccy + ")", store.holdingBinding(row.assetKey, \.costBasis))
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
            TextField("", value: value, format: .number)
                .textFieldStyle(.roundedBorder)
                .multilineTextAlignment(.trailing)
                .monospacedDigit()
                .frame(width: 200)
        }
    }

    private func detailRow(_ label: String, _ value: String) -> some View {
        HStack { Text(label).foregroundStyle(.secondary); Spacer(); Text(value).monospacedDigit() }
        .padding(.vertical, 4)
    }
    private func money(_ v: Double) -> String {
        let f = NumberFormatter(); f.numberStyle = .decimal; f.maximumFractionDigits = 0
        return f.string(from: NSNumber(value: v)) ?? "0"
    }
    private func pct(_ v: Double) -> String { String(format: "%.2f%%", v * 100) }
}