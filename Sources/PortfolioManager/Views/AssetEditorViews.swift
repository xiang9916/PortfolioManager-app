import SwiftUI
import PortfolioCore

private enum AssetClassKeys {
    static let all = ["us_equity", "cn_fixed_income", "us_fixed_income", "greater_cn_equity",
                      "us_reit", "btc", "gold", "jp_equity", "sg_equity", "energy", "other"]
}

/// 添加资产标的 sheet: 标的代码联网校验 + 基础信息。
public struct AddAssetSheet: View {
    @Bindable var store: AppStore
    @Environment(\.dismiss) private var dismiss

    @State private var name = ""
    @State private var ticker = ""
    /// 市场是闭集（`AssetMarket`），也是「池」的唯一来源 —— 不再有手选池的入口。
    @State private var market: AssetMarket? = nil
    /// 当前 market 是不是「按代码自动推断」填的；用户手选过就不再被覆盖。
    @State private var autoFilledMarket: AssetMarket? = nil
    @State private var assetClass = "us_equity"
    @State private var currency = "USD"
    @State private var validating = false
    @State private var validationMessage = ""

    public var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("添加资产标的").font(.title2).bold()
            Form {
                TextField("名称", text: $name)
                TextField("标的代码", text: $ticker)
                    .onChange(of: ticker) { _, newTicker in marketFromTicker(newTicker) }
                Text("如 AAPL / 00700.HK / BTC-USD\n境内基金填 6 位代码，如 000001\n市场会按代码自动推断（.HK→香港、.T→日本、.SI→新加坡、6 位数字/CN_FUND→中国内地、纯字母→美国），可手动改。")
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Picker("市场", selection: $market) {
                    Text("未设置").tag(AssetMarket?.none)
                    ForEach(AssetMarket.allCases, id: \.self) { m in
                        Text(m.displayLabel).tag(AssetMarket?.some(m))
                    }
                }
                .help("池由市场派生：中国内地 → 境内，其余四个市场 → 境外；未设置时按币种回退。")
                Picker("资产类别", selection: $assetClass) {
                    ForEach(AssetClassKeys.all, id: \.self) { k in
                        Text(AssetClassStyle.displayName(k)).tag(k)
                    }
                }
                Picker("币种", selection: $currency) {
                    ForEach(AppStore.currencyOptions, id: \.self) { c in Text(c).tag(c) }
                }
            }
            HStack(spacing: 8) {
                Button { Task { await validate() } } label: {
                    Label(validating ? "校验中…" : "联网校验", systemImage: "checkmark.seal")
                }
                .disabled(ticker.trimmingCharacters(in: .whitespaces).isEmpty || validating)
                if !validationMessage.isEmpty {
                    Text(validationMessage).font(.caption).foregroundStyle(.secondary)
                }
            }
            HStack {
                Spacer()
                Button("取消") { dismiss() }
                Button("添加") { save() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty ||
                              ticker.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding()
        .frame(width: 620)
    }

    /// 代码变化时按 ticker 推断市场（Q33）。只在「市场还没被手选过」时覆盖：
    /// 当前值仍是上一次自动填的结果（或为空）才更新，用户手选后就不再动它。
    private func marketFromTicker(_ raw: String) {
        guard market == nil || market == autoFilledMarket else { return }
        let m = AssetMarket.infer(ticker: raw.trimmingCharacters(in: .whitespaces))
        market = m
        autoFilledMarket = m
    }

    private func validate() async {
        validating = true
        validationMessage = ""
        let res = await store.lookupSymbol(ticker.trimmingCharacters(in: .whitespaces))
        validating = false
        if res.valid {
            // 以数据源返回的币种为准（用标的自身币种填写）
            if AppStore.currencyOptions.contains(res.currency) { currency = res.currency }
            // 名称留空时用数据源返回的名称自动填充（如天天基金 fS_name）
            if name.trimmingCharacters(in: .whitespaces).isEmpty, let n = res.name, !n.isEmpty {
                name = n
            }
        }
        validationMessage = res.message
    }

    private func save() {
        let rawTicker = ticker.trimmingCharacters(in: .whitespaces)
        store.addAsset(key: rawTicker.uppercased(), name: name.trimmingCharacters(in: .whitespaces),
                       ticker: rawTicker,
                       market: market,
                       assetClass: assetClass, currency: currency)
        dismiss()
    }
}

/// 人民币汇率管理 sheet: 自动抓取 + 手动覆盖。
public struct FxRatesSheet: View {
    @Bindable var store: AppStore
    @Environment(\.dismiss) private var dismiss
    @State private var editing: [String: Double] = [:]

    public var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text("人民币汇率（币种 → CNY）").font(.title2).bold()
                Spacer()
                Button { Task { await store.refreshFxRates() } } label: {
                    Label("自动抓取", systemImage: "arrow.clockwise")
                }
            }
            Text("权重计算时会把市值按这里的汇率折成人民币；可直接修改后点完成保存。")
                .font(.caption).foregroundStyle(.secondary)
            List {
                ForEach(store.fxRates.sorted(by: { $0.currency < $1.currency })) { rate in
                    HStack {
                        Text(rate.currency).frame(width: 50, alignment: .leading).monospaced()
                        Spacer()
                        TextField("", value: binding(for: rate.currency), format: .number)
                            .textFieldStyle(.roundedBorder).frame(width: 140).monospacedDigit()
                        Text("CNY").foregroundStyle(.secondary).font(.caption)
                    }
                }
                if store.fxRates.isEmpty {
                    Text("暂无汇率。点「自动抓取」从 Yahoo Finance 拉取 USD/HKD/JPY/SGD 等。")
                        .foregroundStyle(.secondary)
                }
            }
            HStack {
                Spacer()
                Button("完成") { saveAll(); dismiss() }.keyboardShortcut(.defaultAction)
            }
        }
        .padding()
        .frame(width: 460, height: 400)
        .onAppear {
            editing = Dictionary(uniqueKeysWithValues: store.fxRates.map { ($0.currency, $0.rateToCny) })
        }
    }

    private func binding(for ccy: String) -> Binding<Double> {
        Binding(
            get: { editing[ccy] ?? 1.0 },
            set: { v in var c = editing; c[ccy] = v; editing = c }
        )
    }

    private func saveAll() {
        for (ccy, rate) in editing { store.setFxRate(currency: ccy, rateToCny: rate) }
    }
}