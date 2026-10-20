# ADR 0006：缓冲期权 = 四只季度错峰 Max Buffer 的等权篮子

- 状态：已采纳（2026-10-20）
- 关联：ADR 0005、`CONTEXT.md`、`Optimizer/scripts/asset_classes.py`、`Optimizer/scripts/calibrate_params.py`、`Optimizer/scripts/params.py`、`Optimizer/scripts/held_assets.py`、`Optimizer/data/calibrated_params.json`

## 背景

同一系列四只 Max Buffer ETF，按 outcome period 起始月错峰：MMAX（3 月）、MAXJ（6 月）、SMAX（9 月）、DMAX（12 月）。Yahoo 首个交易日分别为 2025-04-01、2024-07-01、2024-10-01、2025-01-02；四只同时存续的共同窗口 = 2025-04-01 → 2026-10-19（390 个交易日）。

旧口径有两个问题：

1. **序列只代表一个 vintage**：校准只用 MAXJ（577 天），它的 cap 与重置时点决定了整条历史；名字却写“MAXJ/DMAX”。
2. **持仓映射断裂**：`us_core_params` 用持仓 **ticker** 去对 `US_INTERNAL` 的 **key**，四只都不等于 `BUFFER` → 不进 `O_US_CORE` 聚合；`held_assets` 的 modeled 判定只看 `SOURCES` 执行符号，只有 MAXJ 在里面 → MAXJ 被判“已建模”但不聚合（等于从模型里消失），DMAX/MMAX/SMAX 被注入成 `[持仓] X` 的 active 腿（10% 上限、纯历史）。

价格口径下：MAXJ 单只 7.16% / 4.99%（与 SPY 相关 0.837）；四只季度再平衡 7.95% / 3.19%（价格相关 0.894）。总回报口径下篮子为 8.73% / 3.08%（与 SPY 相关 0.935）。

## 决策

1. **身份**：`BUFFER` 是 `O_US_CORE` 袖内的一条**等权篮子**（不是顶层大类），成员 = 四只，每季度初（1/4/7/10 月第一个交易日）拉回各 25%。
2. **历史**：取四只同时存续后的共同窗口；每日归一化净值等权平均（季度再平衡）；单日缺口取**交集**（四只一起跳过）；任一成员整段取不到 → 整条篮子本轮不参与校准 + 具名告警。**不向前拼接**。
3. **名称**：`缓冲期权（DMAX/MMAX/MAXJ/SMAX 等权）`（`params.US_INTERNAL["BUFFER"]["name"]`）。`AssetCatalog` 的 `BUFFER` 条目保留为取数代理（`MAXJ`），加注释说明它不是模型序列。
4. **成员映射**：`asset_classes.BUFFER_FAMILY` 单一来源；`us_core_params` 先把成员 ticker 归并到 `BUFFER` 再聚合；`held_assets` 的 modeled 集合加入四只；结果行仍是一行 `[BUFFER]`。
5. **相关表**：`US_INTERNAL_CORR` 与 `SP500_INTERNAL_CORR` 的 BUFFER 行只改宽基三格 —— SPY 0.60→0.93、VTV 0.55→0.80、SPMO 0.60→0.77；单名保留先验（390 天单名估计噪声大，UNH 实测 0.08）。
   注意：`_rebuild_corr` 会用校准文件**整表重建** `US_INTERNAL_CORR`，所以单名先验必须在 `_apply_calibration` 里回填（`_BUFFER_SINGLE_NAME_PRIORS`）；宽基三格不拦，取 JSON 实测。

6. **先验动态化（2026-10-20 续）**：μ_prior(BUFFER) = min((0.5×μSPY先验 + 0.5×Rf_usd)/2, 10%)；μSPY先验 = SPY 的先验（RF_usd + 4.0%），不是 SPY 校准后的 μ；Rf_usd = 数据源美元 10 年期。由校准阶段算好写入 priors / provenance.prior_formula，运行时离线复现；SPY/Rf 缺失 → 回落 RF+2%。

## 兼容性

- `BUFFER` key、结果 JSON 键/顺序、CLI 参数不变；只有 `name` 的值、校准数值、相关表数值变化。
- `calibrated_params.json` 的 BUFFER provenance 加法式增 `members` / `window` / `n_days` / `rebalance` / `rebalance_days` / `series`；旧读者忽略。
- 没有持仓映射进 BUFFER 的用户，`O_US_CORE` 聚合不变（alias 只对四只 ticker 生效）。

## 被否方案

- **保持 MAXJ/DMAX 名字**：名不副实，DMAX 根本没进模型。
- **四只拆成四条 stage-2 腿**：10% 上限被拆碎，四只近乎同质。
- **提升为顶层 `O_BUFFER`**：改两阶段结构，且与美股核心高度重叠。
- **3/4 平均**：口径漂移，看起来一样、其实不是它。
- **只改序列不修映射**：改名与平均都落不到持仓上。

## 后果 / 已知残留

- BUFFER 的 μ/σ 变为约 7.0% / 3.1%（总回报、0.67/0.33 混合、动态先验 3.58% 后），夏普约 0.60（原 0.41）；袖内权重会上升，需要在结果页核对。
- 相关表宽基行抬到 0.77–0.93，避免“低波 + 低相关”的双重高估；单名先验不动。
- 单只持有者的模型风险被篮子摊薄（3.08% vs 单只 2.5–4.8%）；名字里的“等权”与季度再平衡规则是披露。
- 若 App 内某资产的 key 恰为 `BUFFER`，取数仍走 `AssetCatalog` 的 `MAXJ`（代理）；真实持仓应以各自 ticker 建资产，避免取错一只。

## 验证入口

- 新增 `Optimizer/tests/test_buffer_basket.py`（共同窗口、季度再平衡、交集、成员缺失→丢弃、四只→BUFFER 归并、单名先验存活、名称），祖先树必须红。
- 全量 Python（34 文件 / 220 用例）；`--help` 不变。等价门**不再 5/5**：canned 的 US 袖包含 BUFFER 候选，名称/相关格/校准值都会改变输出；diff 35 条全部落在 BUFFER（名称/权重）及相关组合指标，无其它资产参数变化。
- Swift 90 用例；结果页 `[BUFFER]` 名称与“无 `H_` 腿”核对；隐私门。
