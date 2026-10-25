# ADR 0004：持仓纳入候选 + 敏感性锚定

- 状态：已采纳（2026-10-18）
- 关联：`AGENTS.md §12`、`tmp/agent-team/contract-2026-10-18.md`（本机，不入库）

## 背景

两起独立但同源的缺陷（用户实测）：

1. **实盘持有、但不在候选目录内的标的永远不会被选中。**
   主优化的候选池 = 静态目录（`US_INTERNAL` / `BROAD_ASSETS` / 境内开放基金池）；
   持仓只通过 `us_equity.holdings`（且仅 `asset_class == "us_equity"`）进入袖聚合，
   而 `us_core_params` 与 stage-2 又都只认目录内的 key。于是「持仓里有、目录里没有」
   的标的被**三重静默丢弃**；只有「新标的测试」路径（`test_tickers.resolve_test_assets`）
   会把它估成顶层 what-if 腿注入 stage-1 —— 这就是「加入新标的后才能优化出它」的原因。
2. **敏感性曲线与主结果矛盾。**
   扫描逐点用上一点交付的袖参数热启动，会收敛到另一个**自洽但更差**的固定点
   （1–2 轮即 `converged`，不是未收敛）；而 `solve_portfolio` 的多初值择优只在
   「目标播种分叉」触发，扫描路径从不触发，也不和主结果对照。结果：同一目标下
   曲线上的夏普低于结果页交付（用户看到「当前交付」点跑到曲线上方）。

两者共享同一个病因：**答案取决于不可见的输入** —— 候选池里有没有你的持仓、
固定点从哪个初值出发。

## 决策

### A. 持仓纳入（候选池 = 目录 ∪ 未建模持仓）

- App 在 `extract_live.json` 写入加法字段：
  - `held_assets`：全部 `value>0` 持仓（ticker/name/market/asset_class/pool/value_cny/weight）；
  - `include_unmodeled_holdings`：开关（App 置 true；缺键 = 旧行为）；
  - `emit_sensitivity_anchor`：是否输出 `sleeve_params`（见 B；App 置 true）。
- Python 装配层（`Optimizer/scripts/held_assets.py`，两个入口共用）：
  - `modeled`：ticker ∈ US/JP/GC 内部池 key ∪ `market_data.SOURCES` 执行符号 ∪
    本次开放基金池选中代表基金的 6 位代码 ∪ 当前顶层资产 key/fund_code；
  - 其余（`unmodeled`）→ 复用**新标的测试同一套估计器**注入为顶层 what-if 腿：
    key 前缀 `H_`、名称前缀 `[持仓] `、`test_class="active"`（纯历史、10% 单标的上限）。
- 单只数据不可用只记 `skipped` + `held.skipped` 告警，不中断整轮；无 held 字段时
  输出与改造前逐字节一致。
- 结果契约（加法式）：detail `held{included,skipped,counts,corr}`；summary
  `held_assets` + `assets[].is_held` 清单行（必须进清单，否则权重自检会误报漏项）。

**语义边界**：目录外持仓是**顶层腿**，不进入袖内模型（不参与 O_US_CORE 等袖的
μ/σ 聚合与袖内选标），因此不存在双重计价。`us_equity` 类但目录外的持仓会额外得到
`held.us_equity_outside_catalog` 提示。

### B. 敏感性锚定（与主结果同源 + 回退点显式）

- summary 增加 `sleeve_params`（**仅** `emit_sensitivity_anchor` 为真时输出；
  CLI/canned/旧提取文件不多键 → 逐字节兼容）。
- `sensitivity_analysis.py` 新参数：`--anchor-target`、`--anchor-sleeve`、
  `--anchor-ret`、`--anchor-vol`（无锚点时行为与旧版一致）。
- 锚点目标并入扫描网格；**锚点目标点直接用主结果的袖参数作为初值求解**
  （不热启动、不跑第二候选）→ 与结果页同一固定点，交付统计逐位一致
  （`anchor.matched`，容差 1e-6 覆盖 6 位舍入）。
- 其它点（**仅锚定模式**；无锚点路径逐字节不变）：首点直接 canonical 起跑（有主结果
  袖参数 → anchor，否则 λ=0.20 种子）；约束松弛点（target ≤ 切点估计）**直接复用上一
  slack 点的交付结果、不求解**（同一组切点组合、不同目标下限）；绷紧点（切点 < target
  ≤ 锚点目标）用主结果袖参数单次求解（失败才一次独立 prev 重试）；高于锚点目标的点用
  主结果袖参数单次求解、fallback/未收敛即判停。任何重试都是独立单次求解
  （`alt_starts=None`），不会再把 canonical 当候选让引擎重跑一遍 base。
  实测（真实数据 34 个目标点）：旧实现 838.97s → 本版 156.18s（≈2.6 分钟，目标 ≤180s）；
  29 次完整求解 / 34 点（松弛点复用不求解、边界带单次、回退点 fail-fast 取证）。
- 回退点（目标被引擎降级或交付 < 目标）标 `fallback=true`，不画进前沿曲线，
  表格显式标「回退」。
- 顺序扫描**遇到首个回退点即停**（不再追加高价角点与 max-feasible 点）；`--step-max` 约束补点。
- 锚点一致性：目标点被主结果支配（vol > 主 + 1e-4 或 sharpe < 主 − 1e-3）→
  `sensitivity.anchor_mismatch` 告警 + 加法式 `anchor{...}` 块。

### C. 候选包络 + 支配修复（2026-10-25，修「目标↑ 夏普回升」）

- 问题（用户实测）：扫描中段某目标点的夏普低于「更高目标点交付的组合在该目标下」的夏普 ——
  后者在该点同样可行且更优，所以该点不是 max，而是热启动收敛到的**较差固定点**
  （同一目标用主结果袖参数重解得到更优且可交付的点）。用户的定理（固定资产级 μ/Σ 下
  目标↑ ⇒ max 夏普不增）本身成立；问题在实现没有解到 max。
- 决策：扫描结束后做**候选包络**——每个目标 T 的显示值 = 所有可交付候选中
  `ret ≥ T` 的夏普最大者。可行集随 T 收缩 ⇒ 包络必然非增（集合包含关系的直接推论，
  不依赖求解器是否全局最优；每个候选仍是真实交付组合，T 只是下限）。
- **支配修复**：自身解被更高目标的可行候选支配的点，先用主结果袖参数重解一次
  （仅 App 有 `--anchor-sleeve`；每点最多一次），仍不如包络则借用包络并标
  `source_target`。点级加法键 `repaired` / `source_target`（旧输出没有 → nil）。
- 实测（本地复核，数值不入库）：被支配点经一次重解后被替换为更优可行点，曲线在该段恢复单调。
- `monotone_envelope=False` 只用于锁定 2026-10-18 start-policy 的回归测试；
  主运行 / 结果页 / CLI 契约不变（敏感性 JSON 只多加法键与 `scope` 文案）。
- 被否：冻结主结果模型（低目标端的交付夏普明显下降 —— 那是高目标篮子的模型，
  不是低目标交付篮子的模型，语义变成另一条曲线）。包络是**已知候选**的上包络，
  不承诺找到未采样的更优候选（不冒充全局最优证明）。

## 兼容性（为什么这些键是门控的）

- 主运行（`sleeve_start=None`、无 alt、extract 无新字段）**不新增任何键**：
  `sleeve_history[*].zero_weight_sleeves` 仅在外部热启动/alt 路径写；
  `sleeve_restarts.reason` 仅在 alt 路径写。
- 因此 `tmp/slim/equivalence.py`（canned）保持严格 5/5，`optimize_portfolio.py --help`
  逐字节不变；两条新行为都由 extract 字段/新 CLI 参数触发。

## 被否方案

- **把目录外持仓动态塞进 US/JP/GC 袖内部池**：语义更“正确”，但需要动态 μ/σ、
  内部相关矩阵与 SP500 相关表，改动面与回归风险远大于收益；且与新标的测试路径
  口径不一致。
- **主运行也加 λ=0.20 全局多初值**：会改变所有用户的每一次输出，需要独立
  walk-forward A/B 与发布决策，本轮只做敏感性侧；留作后续。
- **按 0 权重袖做固定点动力学硬化**（把空袖参数规范化）：触及核心固定点行为，
  所有低目标档输出都会变，需要 A/B；本轮只加审计键（外部热启动路径）。

## 后果 / 已知残留

- 有「未建模持仓」的用户，主优化结果会把这些标的纳入候选（可能改变建议配置）；
  App 结果页单列「持仓纳入」并给出估计参数，可关闭开关回退。
- 敏感性曲线与主结果在锚点处严格一致；锚点以外用**候选包络 + 支配修复**保证
  「目标↑ ⇒ 夏普不增」（2026-10-25，§C）。包络是已知候选的上包络，不是未采样候选的
  全局最优证明；`source_target` / `repaired` 是加法审计键。
- 主运行仍是单路径冷启动：若冷启动本身落在较差固定点，本轮不额外探索
  （后续若做全局多初值，需单独 A/B）。
- 零权重袖的假设参数仍不参与验证，只在新增加法审计键里可见。

## 验证入口

- `Optimizer/tests/test_held_assets.py`（12 项）、`test_engine_alt_starts.py`（11 项）、
  `test_sensitivity_anchor.py`（19 项）、`test_sensitivity_envelope.py`（5 项）；
  祖先树（`PM_OPT_SCRIPTS`）必红。
- 全量 Python 38 文件 / 249 用例；Swift 202 用例（2 联网跳过，2026-10-25 实测）。
- 真实数据本地复核（结果只写 `tmp/`，不入库）。
