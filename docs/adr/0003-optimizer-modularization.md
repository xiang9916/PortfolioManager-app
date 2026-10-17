# ADR 0003：Python 优化器模块化重构（纯计算内核 + 三种调用器）

- 状态：已接受（2026-10-17）
- 需求方：xiang9916（目标、目录约束、三种调用器划分由需求方给定）
- 影响范围：`Optimizer/scripts/**`、`scripts/build_app.sh`、`AGENTS.md`、`CONTEXT.md`
  （`scripts/privacy_check.sh` **不受影响**：候选池白名单是 basename 正则 `POOL_RE`，只搬目录、不改文件名即命中不变）
- 相关：ADR 0001、0002（Swift 侧分层）；`AGENTS.md §12`（参数口径与求解入口的唯一来源）

## 1. 背景：现在的耦合长什么样（可复核的证据）

规模：`Optimizer/scripts/` 共 6731 行 / 16 个模块，其中 `optimize_portfolio.py` 单文件 2299 行；
`Optimizer/tests/` 共 17 个文件 / 105 个用例（105 而非 102：102 是 16 个文件时的陈旧值）。
它同时承担五件互不相干的事：联网管线编排、候选池装配、参数兜底、纯数学求解、袖内固定点迭代与结果文案。

四处具体耦合（都能 grep 到，不是感觉）：

1. **模块级可变全局**：`TARGET_RETURN` / `RF` / `CAP_HITS` / `CALIBRATION_WARNINGS` 是模块级可变状态（`CAP_HITS` 见 `Optimizer/scripts/optimize_portfolio.py:60/72/2051/2248`）。
   实际传导机制（2026-10-17 独立核验更正）：约束函数把全局当**默认参数兜底**（`L206-207`），`main()` 再**显式传参**覆盖（`L2139-2142`）——
   不是「闭包按全局名查找」；`L2026` 那句「此处重绑后全部生效」的注释**已经过期**（重绑在 `L2027/L2028-2031`，`global RF` 在 `L2040`）。
   结论不变且更值得修：**同一进程里改一个全局会静默改变下游全部行为**，而默认参数把这种耦合藏进了函数签名后面，单测无法隔离。
2. **调用器依赖调用器**：`sensitivity_analysis.py:41` 直接 `from optimize_portfolio import solve_portfolio`。
   后果：第二个调用场景无法独立测试；改结果页入口会连坐敏感性分析。
3. **纯数学落在错误的分层**：`params.py`（数据/参数模块，1081 行）里放着 `higham_psd`（矩阵 PSD 修复，纯数值）。
4. **网络 I/O 没有单一入口**：直接联网/开子进程的代码散布在 **7 个模块** —— `market_data.py`、`bond_prior.py`、
   `check_fund_availability.py`、`fetch_fund_fees.py`、`fetch_macro_rates.py`、`sync_hsbc_funds.py`、`fund_pipeline.py`
   （`calibrate_params.py` 只是**间接**联网：import `market_data` + 落盘 JSON，不计入），
   且没有任何机械约束阻止「内核也去联网」。

代价：本项目的真实失败模式是**口径漂移与静默降级**，而它们通常只能在端到端跑真实数据时才暴露；
单测只能覆盖手工搭桩的片段（`Optimizer/tests/` 17 个文件、105 个用例大多靠受控桩）。

## 2. 目标结构

```
Optimizer/scripts/portfolio_system/
├── models/                      # 纯数据：冻结 dataclass，无行为、无 I/O
│   ├── data_types.py            # AssetSpec / ProblemSpec / SolveResult / Warning / SleeveAudit
│   └── serialization.py         # DTO <-> 既有 JSON 契约（键名与顺序的唯一来源）
├── core/                        # 【纯计算】只做数学，禁网络/禁磁盘/禁子进程
│   ├── errors.py                # 异常分级（见 §6）
│   ├── linalg.py                # project_psd / higham_psd / build_corr_matrix / portfolio_stats
│   ├── constraints.py           # 上下界、active 10% 上限表、池约束、可行性判定
│   ├── solver.py                # solve_min_var / solve_max_sharpe / max_feasible_return
│   ├── sleeves.py               # 袖内固定点迭代引擎（含回退/播种/择优/审计字段）
│   └── optimizer.py             # PortfolioOptimizer 接口 + LocalConvexOptimizer 实现
├── callers/                     # 业务编排：取数 → 组装问题 → 驱动内核 → 汇总
│   ├── base_runner.py           # Runner 接口 + RunRequest / RunOutcome
│   ├── preset_runner.py         # 预设调用器（当前持仓 + 预设资产）
│   ├── sensitivity_runner.py    # 敏感性分析器（多目标点，逐点完整流水线 / 可并行）
│   └── new_asset_runner.py      # 新标的分析器（基础资产 + 人工指定新标的）
├── services/                    # 基础设施：唯一允许联网/落盘/开子进程的地方
│   ├── data_fetcher.py          # 行情序列抓取（包装 market_data，唯一网络入口）
│   ├── pipeline.py              # 在售/申赎状态子进程（包装 fund_pipeline）
│   └── portfolio_source.py      # 读取持仓/配置载荷（extract JSON），不直连数据库
└── cli/
    ├── optimize_portfolio_cli.py
    └── sensitivity_cli.py       # 保留既有 argparse 契约，参数名/语义一字不改
```

依赖方向（单向，无环）：

```
   cli/ ──▶ callers/ ──▶ core/ ──▶ models/
              │
              └──▶ services/ ──▶ models/

   core/  ✗──▶ services/     （机械禁止：见 §3 R1）
   callers/ ✗──▶ callers/    （机械禁止：调用器之间不得互相 import）
```

### 为什么落在 `Optimizer/scripts/portfolio_system/` 而不是仓库根

`Optimizer/scripts` 是**已经存在的外部契约**，不是内部目录选择：

- `scripts/build_app.sh:75`：`cp -R "Optimizer/scripts" "${BUNDLE}/Contents/Resources/Optimizer/scripts"`；
- `Sources/PortfolioCore/Optimizer/OptimizationService.swift:123`：`sidecar.scriptURL("optimize_portfolio.py")`；
- `Sources/PortfolioManager/AppStore.swift:34,37`、`Sources/pm-cli/main.swift:30`：同样按 `Optimizer/scripts` 解析；
- `Optimizer/tests/` 11 个文件用环境变量 `PM_OPT_SCRIPTS` 指向「另一棵树的 `Optimizer/scripts`」做祖先对照。

把包放在这一层，上述四处**一行都不用改**，且 `PM_OPT_SCRIPTS` 自动把整个包一起搬过去（祖先对照继续有效）；
Python 又会自动把「脚本所在目录」放进 `sys.path`，连路径引导都不需要。
把包放到仓库根则：DMG 里不会被打包（运行时 ImportError）、三处 Swift 资源路径全要改、`PM_OPT_SCRIPTS` 语义被打破。
→ 因此阶段 P0–P3 就地落盘；把包上提到 `Optimizer/portfolio_system/` 是**独立的 P4**，
必须在同一次改动里同步 `build_app.sh` + 三处 Swift 资源路径 + `PM_OPT_SCRIPTS` 语义 + 重新打包验证。

## 3. 铁律（每条都要有机械校验，不靠自觉）

| # | 规则 | 校验手段 |
|---|---|---|
| R1 | `core/**` 与 `models/**` 不得 import 网络/磁盘/子进程（`urllib` `requests` `socket` `ssl` `subprocess` `sqlite3` `os.system` 及任何 `services.*`），只允许 `numpy`/`scipy`/标准数学库 | AST 静态检查测试（见 R7），并禁止在 `core` 里出现 `open(` |
| R2 | `core/**` 不得有模块级可变状态（无 `global`、无模块级 dict/list 被原地修改） | AST 检查 `global` 语句 + 人工评审 |
| R3 | 调用器之间不得互相 import；三者只依赖 `core`/`models`/`services` | AST 检查 `callers/*` 的 import 列表 |
| R4 | CLI 参数名与语义、输出 JSON 的键名与语义**字节级不变** | 等价性测试（R7）+ 站内快照对照 |
| R5 | 既有 17 个测试文件 / 105 个用例只增不减、全绿 | **逐文件跑**（venv 无 pytest）：`for f in Optimizer/tests/test_*.py; do Optimizer/.venv/bin/python3 "$f"; done` |
| R6 | 包内不得出现真实持仓信息（标的/数量/成本/金额/组合构成）；验证记录只进 `tmp/`（gitignore） | `scripts/privacy_check.sh` + 人工 |
| R7 | 新旧两条路径对同一输入必须给出**逐字段相同**的结果 | `Optimizer/tests/test_refactor_equivalence.py` |

## 4. 接口规格（骨架契约）

### 4.1 DTO（`models/data_types.py`，全部 `@dataclass(frozen=True)`）

```python
Warning(code: str, message: str, severity: str)          # code 取自既有 WARNING_CODES（唯一来源）
AssetSpec(key, mu, sigma, market, asset_class, upper, lower, extra)
PoolSpec(domestic_weight, overseas_weight)               # 对应既有的 dom_w / ov_w
GoalSpec(target_return, rf, active_cap, max_sleeve_iter, damping)
ProblemSpec(assets, corr_pairs, pools, goal)             # 内核唯一输入
SolveResult(weights, stats, sleeves, warnings, selected_round, deliverable, final_source, audit)
```

要点：`ProblemSpec` 里出现的每个数都必须是**已经算好的数**（μ/σ/相关性/上下界/目标/rf），
内核不再回头去问「参数模块」或「行情模块」要任何东西 —— 这就是「纯」的操作定义。

### 4.2 优化器接口（`core/optimizer.py`）

```python
class PortfolioOptimizer(ABC):
    @abstractmethod
    def optimize(self, problem: ProblemSpec) -> SolveResult: ...
```

实现 `LocalConvexOptimizer`（SciPy SLSQP 多起点）。未来换算法（CVXPY / 二次规划 / 商业求解器）只新增实现类，
调用器一行不改 —— 这是「易于更换优化算法」的落点。

### 4.3 调用器接口（`callers/base_runner.py`）

```python
class Runner(ABC):
    @abstractmethod
    def run(self, request: RunRequest) -> RunOutcome: ...

class PresetRunner(Runner):      # 持仓 + 预设资产 → 一个问题 → 一次求解
class SensitivityRunner(Runner): # 多目标点 → 每个点一次完整流水线（可热启动、可并行）
class NewAssetRunner(Runner):    # 基础资产 + 人工指定新标的 → 先解析新标的序列再求解
```

三个调用器共享的取数与装配逻辑放在 `callers/base_runner.py` 的**辅助函数**（或 `services` 里的函数）里，
**不靠继承调用器**实现复用（避免又长出「调用器基类里塞业务」的老路）。

### 4.4 数据源接口（`services/`）

```python
class DataFetcher(ABC):      # fetch_series(symbol, window) -> Series；唯一允许联网的接口
class PortfolioSource(ABC):  # load(extract_path) -> HoldingPayload；只读载荷，不直连数据库
```

**关于 `portfolio_repo`**：本项目的持仓与配置**不在 Python 侧**，由 Swift 侧读 SQLite 后写成 extract JSON
交给优化器（见 `AGENTS.md §6`）。因此没有「Python 数据库仓储层」可拆，
`services/portfolio_source.py` 是**载荷适配器**（解析 extract JSON → DTO），不是 DB 层。
这一点必须写进文档，否则后来者会去造一个不存在的数据库耦合。

## 5. 数据流（解耦后）

```
PresetRunner.run():
  PortfolioSource.load(extract) ─▶ 持仓/配置
  services.pipeline.ensure()    ─▶ 在售/申赎状态（唯一子进程/联网）
  DataFetcher.fetch_series()    ─▶ 行情序列
  装配 AssetSpec/ProblemSpec（参数口径仍在 asset_classes.py：唯一事实来源）
        └─▶ PortfolioOptimizer.optimize(ProblemSpec) ─▶ SolveResult
              └─▶ 结果汇总（键名契约唯一来源）──▶ 结果页 JSON

SensitivityRunner.run():
  复用 PresetRunner 的取数与装配（共用装配函数，不共用求解循环）
  for T in targets: ProblemSpec(T) ─▶ optimize()   # 上一点热启动；可并行

NewAssetRunner.run():
  基础装配 ─▶ 为新标的 fetch_series + 按类别估 μ/σ ─▶ 追加 AssetSpec
        └─▶ PortfolioOptimizer.optimize()（其余与 PresetRunner 完全同路径）
```

一致性收益：三个调用器走**同一个** `optimize()` 与**同一份**装配函数，
「结果页与敏感性图上同一个目标点给出互相矛盾的数字」在结构上不再可能（该 bug 的历史见 `AGENTS.md §12`）。

## 6. 异常分级与降级矩阵（内核不得抛「裸异常」）

```
OptimizerError
├── InputError            # 输入本身不合法：资产为空、σ≤0/NaN、上下界冲突、目标为 NaN
│                         #   → 立即失败，带 key/取值/来源；绝不静默继续
├── NumericalError
│   ├── SingularCovariance # 相关性矩阵奇异/非 PSD
│   │                     #   → 先 project_psd（特征值下限）修复；仍不可用则丢弃该资产并报警
│   └── SolverFailure     # SLSQP 未收敛 / 返回越界点
│                         #   → 换起点重试（多起点）；全失败则该袖标记不可交付
└── InfeasibleTarget      # 组合层目标不可行
                          #   → 交付「最大可行收益」解 + 显式告警（交付收益低于目标）
```

降级一律**显式**：每一次降级都必须产生一条带稳定 `code` 的 `Warning`，并进入结果审计字段；
禁止「静默回退 + 只在日志里提一句」——那正是本项目历史上最贵的一类 bug（见 `AGENTS.md §12`）。
内核不做 I/O，所以降级策略本身可单测：给定一个奇异矩阵 / 不可行目标，断言异常类型与告警 code。

## 7. 迁移阶段与验收门（strangler：每阶段独立可验、可回滚）

| 阶段 | 内容 | 验收门 |
|---|---|---|
| **P0** | 落包骨架：`models` / `core` / `callers` / `services` / `cli`；接口与 DTO 定稿；`core` 内以**委托**方式调用既有 `solve_min_var`/`solve_max_sharpe`；脚本入口保持原样不动 | 17 个既有测试文件全绿；新增 R1/R3 静态检查通过；`grep` 证明未改既有脚本行为 |
| **P1** | 等价性门：`test_refactor_equivalence.py` —— 同一受控桩输入，旧路径（`solve_portfolio`）与新路径（`PresetRunner`+`LocalConvexOptimizer`）**逐字段比对**；并证明该门**能失败**（人为扰动一次，必须红） | 等价测试绿 + 扰动实验留证（`tmp/`） |
| **P2** | 把纯数学从 `optimize_common.py` / `params.py`（`higham_psd`）搬进 `core/`，`optimize_common` 变薄再导出壳；消除 `TARGET_RETURN`/`RF` 等全局，改为 `GoalSpec` 显式传参 | 每搬一个函数跑一次等价测试；全局名 grep 归零 |
| **P3** | 把编排从 `optimize_portfolio.py` 搬进 `callers/` + `services/`；`sensitivity_analysis.py` 不再 import `optimize_portfolio`；脚本入口变薄壳 | 三个调用器各自的单测（不联网、用桩）独立通过；`sensitivity_analysis` 的 import 图不再指向调用器 |
| **P4** | 包上提 + 打包路径切换（`build_app.sh`、三处 Swift 资源路径、`PM_OPT_SCRIPTS` 语义），删除薄壳 | DMG 重新打包 + 解压回验 + 一次真实结果页/敏感性端到端 |

**不变式（贯穿全部阶段）**：CLI 契约、JSON 键名、告警 code、既有用例数只增不减；
每个阶段结束都必须能用一句话说明「行为变了没有」，且用测试而不是用眼看。

## 8. 未采纳的方案

- **一次性重写（big-bang）**：把 6731 行按新结构重写一遍再切换。
  否决理由：本项目最有价值的资产是 105 个用例与「低目标档逐字节不变」这类不变式，
  它们保护的是**已发生的真实事故**（回退角点、袖全空误判收敛、告警只反映中间轮…）。
  big-bang 会让这些不变式在切换瞬间全部失效，且没有任何机械手段分辨「等价重写」与「静默降级」。
- **按用户目录直接放仓库根**：见 §2 末（会破坏打包契约）。
- **在 Python 侧引入数据库仓储层**：见 §4.4（不存在这种耦合，属虚构分层）。
- **引入依赖注入框架 / 重型 DI**：三处调用器、两个实现的规模用构造函数传参与 ABC 足够，
  引入框架只会把「隐式全局」换成「隐式容器」。

## 9. 残留风险

1. 袖内选标是离散的，**不承诺无条件收敛**（既有结论，本重构不改变这一点，只把它变成可单测的显式分支）。
2. `params.py` / `asset_classes.py` 的参数口径仍是全局事实来源且体量大；P2 只动分层，不动口径。
3. 打包路径切换（P4）是唯一会同时触碰 Swift、脚本与构建脚本的阶段，必须单独发布验证。
4. `scripts/privacy_check.sh` 的候选池白名单是 **basename 正则**（`POOL_RE`，6 项），所以**搬目录不受影响**；
   真正的风险是**重命名**：改 `params.py` / `market_data.py` / `AssetCatalog.swift` / `calibrated_params.json` 的文件名时，
   必须同步更新 `POOL_RE`，否则隐私自查会漏检。

## 10. 本 ADR 的勘误（2026-10-17 独立核验）

设计文档的事实主张必须被独立复核。本轮由 `verify` 就地去代码复核 16 条主张，**5 条复现失败、2 条机制/精度写错**，
其中 3 条会直接误导后续阶段，已在上文更正（核验报告在 `tmp/agent-team/verify-report-adr0003.md`，不进仓库）：

| 原主张 | 实测 | 影响 |
|---|---|---|
| `PM_OPT_SCRIPTS` 使用者 12 个文件 | **11** 个测试文件 | 低（计数） |
| 测试 17 文件 / 102 用例 | **17 文件 / 105 用例** | 中：会把「用例只增不减」的基线记错 |
| 直接联网模块 8 个（含 `calibrate_params.py`） | **7** 个（该文件仅间接联网） | 中：P3 的收敛目标清单写错 |
| R5 校验手段 `python -m pytest Optimizer/tests` | venv **无 pytest**，测试是逐文件 `main()` 脚本 | 高：验收步骤**不可执行**，会让人以为测试没跑起来 |
| `privacy_check.sh` 写死路径、搬动要同步 | 实为 **basename 正则** `POOL_RE` | 中：把不存在的风险写进了计划，同时漏掉真正的风险（**重命名**） |
| 全局耦合机制 = 闭包按全局名查找 | 实为**默认参数兜底 + `main()` 显式传参**；`L2026` 注释已过期 | 中：结论对但机制错，会导致 P2 去改错的地方 |

**两条关键正面结论（复核通过）**：§4.4「Python 侧不存在数据库耦合」成立（16 个模块全扫无 `sqlite`/`connect`/表格读取）——
所以「Python 侧仓储层」是**不存在的耦合**，不要为了对称去造一个；
§7 的 P0/P1 加法式落点在打包与导入两个维度上均可复现（核验当时 `portfolio_system/` 尚不存在、既有脚本 0 处 import 它）。

教训（写给后续阶段的自己）：**架构文档里的行数、行号、清单、命令，都要像代码一样被核验**；
本轮错的那条「pytest」如果没被抓到，P0 的验收就会变成一句无法执行的命令，而没人会发现自己其实没跑测试。

