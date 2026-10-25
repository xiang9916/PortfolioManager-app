# ADR 0010 — 资产身份的单一入口与目录契约

- 状态：已采纳（2026-10-24）
- 范围：Sources/PortfolioCore/Identity/AssetIdentity.swift、AssetCatalog、Database、BackupManager、PortfolioImporter、PortfolioValuation、AppStore、pm-cli；Python 的 asset_identity.py、held_assets、test_tickers、params、core/constraints。Schema 不变。
- 关联：ADR 0002（池由市场派生）、ADR 0009（组合估值单一入口）。

## 背景

「这个资产是谁」没有单一 module：代码规范化散在 5 处（Swift 的 AssetMarket.infer 与 AppStore.fundCode，Python 的 held_assets.harmonize_held_ticker、test_tickers.classify_source、sync_hsbc_funds），取数解析有 3 套（App / pm-cli / Python 注入）。后果是同一个字符串可以有四种答案：123456.CN_FUND 被判成 CN 市场、却被 App 送给 Yahoo（404）、被 Python 持仓路径当成基金、被 Python 测试路径当成 Yahoo。

资产目录也有两份：Swift AssetCatalog 61 项、Python SOURCES 46 项；交集 42、Swift-only 19、Python-only 4（模型里有、App 按 key 取不到）。同一只基金在两侧各有一个 key（QDII 全球股票、红利低波 100 各一对），另有一条 Python 代理表引用已不存在的 key。历史上一次代码重命名（SPLG→SPY）动过 11 个文件，没有测试把两侧绑在一起。

池/币种回退也不一致：AssetMarket.pool 有币种回退，但组合估值对「没有 assets 行的持仓」硬编码境外；Python 参数表仍用 cross 表示「不参与境内/境外池约束」，而 CONTEXT.md 明确把 cross 列为 Avoid；core/engine 与 core/constraints.pool_indices 对同一资产给出不同的池成员。

生命周期同样无主：assets.key 是 6 张表的连接键，没有改名路径；删除标的漏删 quotes（重建同 key 会复活旧报价）；Schema 声明了 7 个 FOREIGN KEY(assets.key) 但全仓未启用 PRAGMA foreign_keys，约束是装饰性的。

## 决策

1. **身份的唯一入口**：Swift 的 AssetIdentity 与 Python 的 asset_identity.py 是唯一的代码规范化 + 取数源解析入口，两侧同一张规则矩阵。AssetMarket 继续拥有市场词表（别名 / 推断），身份模块只做组合，不复制市场表。
2. **代码规范化**：去空白 → 大写 → 6 位境内基金代码去掉 .CN_FUND / .SZ / .SH 后缀（大小写不敏感）；.SS 是 A 股 Yahoo 后缀，故意不属于基金；只有恰好 6 位 ASCII 数字才算基金代码。
3. **取数优先级**：ticker（空则 key）的基金代码 → AssetCatalog.ref(for: key) → Yahoo(规范化后的代码)；空 ticker 按 key 兜底。
4. **目录两层**：modeled 与 Python SOURCES 逐 key 相同（kind + symbol），由 Optimizer/tests/test_asset_catalog_parity.py 机器校验；legacy 只为本机已有资产与旧备份保留，不参与契约。modeled 补齐了 Python 有、Swift 原先缺失的 4 个模型 key。
5. **池的唯一回退规则**：市场 → 池；市场解析不出 → 币种回退（CNY → 境内）。无 assets 行的持仓按「key 当 ticker + 持仓币种」走同一条规则，并产生 missingAsset 告警（替换原先硬编码境外）。
6. **生命周期**：deleteAsset 连 quotes 一起删。**不启用** PRAGMA foreign_keys：pm-cli refresh 会写不属于 assets 的 prices，启用即失败；正确做法是迁移 + 全链路审计，另开任务。仍不做 key 改名/合并。
7. **Python 词汇与边界**：参数表用 unconstrained（原 cross）表示「不参与境内/境外池约束」；core/constraints.pool_indices 的境外成员改为「等于 overseas」，与 core/engine 对齐。该改动只影响单层 DTO 路径的随机起点，生产 sleeve 路径不变（等价门验证）。
8. **归一化收敛**：normalizeAssetClassification 对落库原文做精确比较，us / 两端空白 / 自由文本 / 空串都会收敛成 rawValue 或 NULL，且幂等。

## 被否方案

- **用 Python 生成 Swift 目录（构建期代码生成 / 共享 JSON）**：需要改打包与资源加载，收益要等目录继续变大才明显；当前用文本契约测试就能守住「modeled == SOURCES」。
- **从市场推断币种**：把「敞口币种」和「持仓币种」混为一谈；CNY 基金 / QDII 会立刻错。
- **现在就启用外键**：会打断 pm-cli refresh 与既有测试的写入顺序，且需要数据迁移与全链路审计。
- **做 key 改名 / 合并**：需要 UI + 数据迁移；当前用「删除 + 重建」并记录代价。
- **保留无资产行硬编码境外**：与 AssetMarket.pool 的文档规则矛盾，且让数据不完整的库静默偏移。

## 后果

- 带后缀（任意大小写）的基金代码现在走天天基金；以前是 Yahoo 404（无历史价时市值 0）。
- ticker 为空的资产改按 key 兜底：市场/池与估值口径一致；下次启动 normalize 会把 market 写成闭集值（缓存列，不是结构迁移）。
- 无 assets 行的 CNY 持仓从境外变为境内，并出现 missingAsset 告警；只影响数据不完整的库。
- 删除标的会删掉 quote；重建同 key 不会复活旧报价。
- 新 UI 资产的 key/ticker 写规范形式；旧 key 不迁移（两种 key 共存，都能取数）。
- cross → unconstrained；pool_indices 与 engine 对齐；结果 JSON / 备份 JSON / Schema / CLI 参数均无变化。
- pm-cli refresh 支持 6 位基金代码与补齐的 4 个模型 key（原先报「未知标的」）。

## 验证入口

- Tests/PortfolioCoreTests/AssetIdentityTests.swift（12 项，纯 fixture + 临时库）
- Tests/PortfolioCoreTests/AssetCatalogTests.swift（4 项）
- Tests/PortfolioCoreTests/PortfolioValuationTests.swift（17 项，含无资产行 CNY/USD）
- Optimizer/tests/test_asset_identity.py（7 项）
- Optimizer/tests/test_asset_catalog_parity.py（5 项，跨语言契约）
- Optimizer/tests/test_refactor_equivalence.py（等价门，pool_indices 改动后仍全绿）

## 已知限制

- 不做 key 改名/合并；同一资产可能以旧 key 与规范 key 并存，不会被自动合并。
- 外键仍未启用（见决策 6）。
- AssetRef.name 仍是展示提示，不参与契约（漂移不影响取数）。
- Python 的身份模块放在 scripts（上层），没有下沉到 core；它不 import params / market_data，保持纯函数。
- legacy 层会随旧备份长期存在；不要在 legacy 里新增模型标的。
