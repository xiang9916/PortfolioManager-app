# ADR 0007：IALT 注册为 strategy 顶层腿 + 短样本置信区间保守口径

- 状态：已采纳（2026-10-22）
- 关联：ADR 0005、ADR 0006、`Optimizer/scripts/asset_classes.py`、`Optimizer/scripts/calibrate_params.py`、`Optimizer/scripts/market_data.py`、`Optimizer/scripts/params.py`、`Optimizer/scripts/held_assets.py`、`Optimizer/portfolio_system/callers/preset_runner.py`、`Optimizer/data/calibrated_params.json`、`Sources/PortfolioCore/DataSources/AssetCatalog.swift`

## 背景

IALT = iShares Systematic Alternatives Active ETF（NASDAQ:IALT，USD），2025-12-11 上市，基准 ICE BofA 3-Month U.S. Treasury Bill Index；多策略绝对收益（股票市场中性 / 分散债券 / 管理期货）。

Yahoo 复权序列只有 **215 个交易日**（2025-12-11 → 2026-10-20），历史统计为 μ=22.63% / σ=7.53%（含 2 次分红，adjclose 可用）。

问题：按 strategy 的标准口径（μ = 67% 历史 + 33% 先验，先验 = 美元 RF + 2.0%），10 个月样本会把 μ 顶到
**17.52%**、σ 7.53%、单资产夏普 1.64；把历史权重人为压到 0.35 也只到 12.57% / 0.99 —— 仍然远离
"一只以 3 个月美债为基准的基金能长期交付的收益"。真正的问题不是权重系数，而是 **215 天样本对 μ 的估计本身很宽**。

（组合层面的权重变化属本地验证记录，按 AGENTS §1 不入库：只在对话 / `tmp/` 里保留。仓库只记机制与单资产口径。）

## 决策

1. **层级**：`O_IALT` 是**顶层 strategy 腿**（`BROAD_ASSETS`，pool=overseas），**不进** `US_INTERNAL` —— 另类绝对收益敞口不是"美国核心权益"，放进袖会被 stage-2 误当成美股内部候选。
2. **先验**：`RF_PREMIUM["O_IALT"] = (+0.020, "usd")`（本次校准美元 RF 5.13% → 7.13%；RF 由数据源每次校准注入），兜底 `PRIOR["O_IALT"] = 0.070`（0.5% 粒度；audit_table 要求 strategy 必须有先验）。
3. **短样本保守口径**（`asset_classes.CONSERVATIVE_CI = {"O_IALT": {"level": 0.95}}`）：校准阶段先把历史统计收缩到置信边界，再走类别混合：
   - `SE(μ) = σ_hist / √(n/252)`；`μ_low = μ_hist + t_{0.025, n−1} · SE(μ)`
   - `σ_high = σ_hist · √((n−1)/χ²_{0.025, n−1})`
   - 实测：`μ_low = 6.56%`（t=−1.9711、SE=8.15%）、`σ_high = 8.32%`（χ²=175.4）→ 混合后 **μ = 6.75%、σ = 8.32%**。
   - **这是谨慎口径、不是无偏估计**：单边 95% 各取一次，联合含义约 90%。它的用途是"别把上市初期行情当可持续预期收益"，不是"预测这只基金未来只有 6.75%"。
4. **无长历史代理**：`PROXY_MAP["O_IALT"] = None`。QAI/DBMF 等是多策略复制/管理期货，不是同一敞口；按 ADR 0006（BUFFER）先例，无同质代理就保留短样本，用 CI 抑制噪声。
5. **相关性不手写**：校准按 215 天共同窗口实测（不足 180 天自动 0.30 填充）。
6. **结果页/取数**：`_EXEC_CODE["O_IALT"] = "IALT"`；`AssetCatalog` 增 `AssetRef("O_IALT", .yahoo, "IALT")`。
7. **范围**：`CONSERVATIVE_CI` 本轮只登记 IALT；机制可复用，未来给其它短样本标的启用只需加一行。
8. **复评条件**：IALT 历史达到约 2–3 年后移除 CI 条目、回到类别默认 2/3 混合（届时 CI 宽度会自然收窄，也可以只把 level 降到 0.90）。

## 兼容性

- 结果 JSON/CLI/summary/warning codes/Schema v8 **不变**；`assets` 多一行 `[IALT] …`、`stage1.weights` 多一个 `O_IALT` 键。
- `calibrated_params.json` 的 `O_IALT` provenance 额外带 `mu_input` / `vol_input` / `ci_bounds{level,n,dof,t,chi2,se_mu,mu_hist_raw,vol_hist_raw,mu_low,vol_high}`（加法式，旧读者忽略）。
- 持有 IALT 时：ticker `IALT` 命中 `market_data.SOURCES` 的执行符号 → 判为 modeled，**不再注入 `H_IALT`**，也不再触发 `held.us_equity_outside_catalog`（若该持仓类别标成 `us_equity`，它同样不进 `O_US_CORE` 聚合 —— 建议在 App 里把类别选「其他」）。
- 其余标的的校准数值会因新增两日数据轻微漂移（每次重跑都会），不属于本决策。

## 被否方案

- **标准 0.67**：10 个月样本直接把 μ 顶到 17.5%、单资产夏普 1.64 —— 把上市初期行情当预期收益。
- **人工收缩 0.35**：任意且基本无效（μ 仍有 12.6%、单资产夏普 0.99），还要维护一个没有统计含义的系数。
- **挂 QAI 代理**：不同策略/不同管理人，模型测的就不是 IALT 了；且会掩盖它真实的短样本问题。
- **放进 `US_INTERNAL`**：语义错误（这不是美股核心权益），且会被 stage-2 当成袖内候选。
- **写死 μ/σ（`SPECIAL_FIXED`）**：该项目只给货币基金这种"μ/σ 全写死"的标的用；IALT 有真实序列，应保留数据来源可追溯。

## 后果 / 已知残留

- **系统性低估**：CI 口径下 IALT 的预期收益被压到 6.75%、σ 抬到 8.32%，单资产夏普约 0.20（原始 1.64）。优化器可能给 0 权重或小仓位 —— 这是预期行为，结果页会显示保守口径。
- CI 宽度约 ±16pp 主要由 n=215 决定；样本变长后自然收窄，2–3 年后复评。
- 相关性只有 215 天实测，噪声大；不手写、不额外平滑。
- 预演是 stage-1 近似（不含袖内固定点 / 阶段二、池拆分是虚构值）；正式流水线的最终权重以实现后的复核数字为准。

## 验证入口

- 新增 `Optimizer/tests/test_ialt_registration.py`（11 项：注册完整性、无代理、CI 数学（样本越短越宽 / level 越高越宽 / 退化输入）、校准 provenance 与混合值、相关性覆盖、params 暴露、持仓 modeled、`_EXEC_CODE`）。
- 扩展 `test_asset_classes.py::test_proxy_and_internal_pool_cleanup`：断言 `PROXY_MAP["O_IALT"] is None`。
- Swift：`Tests/PortfolioCoreTests/AssetCatalogTests.swift`（目录条目）。
- 全量 Python（35 文件）+ Swift 90 用例；隐私门（候选池白名单应含 IALT）。
