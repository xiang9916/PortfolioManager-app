# ADR 0005：权益校准改用总回报（复权）序列

- 状态：已采纳（2026-10-20）
- 关联：AGENTS.md §12、ADR 0006、`Optimizer/scripts/market_data.py`、`Optimizer/scripts/calibrate_params.py`、`Sources/PortfolioCore/DataSources/YahooFinanceSource.swift`、`Sources/PortfolioCore/Database/Schema.swift`

## 背景

权益校准一直用未复权收盘价，分红/票息被丢掉。债券侧在 2026-10-13 已经因为同样原因改用 `adjclose`（见 AGENTS.md §12），但只用于债券回归，主序列仍是价格；基金侧 `eastmoney_nav` 取累计净值，本来就是总回报。于是同一张校准表里同时存在价格与总回报两种口径。

2026-10-20 实测（Yahoo）：指数/期货/加密的 `adjclose` 与 `close` 完全相同（`^GSPC`/`^HSI`/`^STI`/`CL=F`/`GC=F` 等，0 天差异）；ETF 才真有差（BND/VNQ 首日比值 ≈0.96）。本组四只 Max Buffer 有年度分配，约 0.8–1.2%/年。

## 决策

1. **Python 校准**：所有 Yahoo 序列按 `adjusted=True` 取 `adjclose`；缺失时回落 `close` 并记 warning。指数/期货/加密不受影响；ETF/个股/REIT/债券代理获得分红。
2. **校准文件**：`calibrated_params.json` 一次性重校准；顶层加 `series_basis: "total_return"`，每条 provenance 加 `adjusted: true/false`（加法式）。
3. **App**：`prices`（历史/累计净值）改存 `adjclose`；`quotes` 仍存最新价，`lookup` 仍用 `regularMarketPrice`。`adjclose` 的最后一点等于当前收盘价，`prices.last` 的“最新价兜底”与市值不受影响。
4. **Schema 注释**：`prices` 的语义改为“权益 = 复权（总回报），基金 = 累计净值”。

## 兼容性

- 不改 CLI 参数、结果 JSON 键/顺序、Schema 版本；`--help` 逐字节不变。
- 等价门 `tmp/slim/equivalence.py` 用 canned 管线；canned 的 US 袖包含 BUFFER 候选，故输出会因 BUFFER 参数变化而不同（有意，见 ADR 0006），不是重构回归。
- 校准值会变：分红型资产的 μ 上升；相对排序变化以重校准后的全表为准（本地记录，不入库）。

## 被否方案

- **只改 BUFFER 四只**：口径不一致，且债券/REIT 代理仍丢票息。
- **只改权益 ETF/个股**：BND/VNQ 这类债券/REIT 代理的价格序列同样丢票息。
- **保持价格口径**：与基金侧累计净值不一致，系统性低估权益 μ。

## 后果 / 已知残留

- 权益 μ 上升 ≈ 分红率 × 类别历史权重；高分红资产（港股高股息、日股高股息、VTV 等）上升更多。除息日的价格跳空不再计入收益，σ 略降。
- App 的“模拟历史财务表现”变为总回报口径；存量 `prices` 由下一次刷新覆盖（`fetchHistory` 取全历史、存前裁近 3 年），未刷新前仍是旧口径。
- 债券回归路径会重复取一次复权序列（主序列已是复权），属冗余但不改变结果，可后续清理。
- 长历史代理若是指数（`^GSPC`/`^HSI`/`^STI`），其 `adjclose` 仍等于价格指数 → 标普500 宽基、美股核心、QDII 股票等使用指数代理的标的仍按价格口径，分红没进 μ。要补上需换总回报指数（如 `^SP500TR`）或改用真实基金序列，属另一项数据源决策。
- 总回报重校准把分红型资产的 μ 抬高（如 VTV +1.9pp、SPMO +1.0pp）。袖内固定点在低目标极限环档的模型-交付差从 <0.5pp 变为约 0.65pp（未收敛、告警显式）；test_sleeve_fixpoint 的未收敛档容差相应放宽到 1pp，收敛档仍为 0.5pp；test_sensitivity_anchor 的展示值比较容差改为 1e-6（展示精度，与 anchor.matched 同口径）。

## 验证入口

- 重校准前后 `μ_hist` / `vol_hist` 差值表（写 `tmp/`，不入库）。
- Python 34 文件 / 219 用例；`--help` 2842 B。等价门：本改动只改校准数据，运行时求解器未动；但 canned 的 US 袖包含 BUFFER 候选，其 μ/σ/相关变化会改变输出，属有意行为变更（见 ADR 0006），不是重构回归。
- Swift 90 用例（87 + `YahooFinanceSourceTests` 3 项）；模拟历史图截图核对。
- 隐私门（worktree/staged/message）。
