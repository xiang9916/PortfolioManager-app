# AGENTS.md — PortfolioManager 开发须知

> 给在此仓库工作的 AI / 开发者。**2026-10-02 汇总**（上一版 2026-09-08）。
> ⚠️ **本文件在仓库内、会随仓库一起公开** —— 所以这里只写可公开的事实：不含真实持仓（代码/数量/成本/金额）、
> 不含组合规模与市场构成、不含个人表格名与构建机绝对路径。内部细节请留在仓库外，不要写进本文件。动代码/发布前先读一遍。

## 0. 现状速览
- 原生 macOS (SwiftUI) 个人投资组合 App，替代「手工表格维护 + 终端跑 Python 优化器」。
- 当前版本 **v0.6-beta.4**（版本号只存在于 `scripts/Info.plist`）；Schema **v8**；GPL-3.0；作者 xiang9916。
- 线上仓库：`xiang9916/PortfolioManager-app`，26 个 pre-release + 27 个 tag 齐全。
- git 仓库根 = 本文件所在目录；父目录本身不是 git 仓库（勿在父目录里跑 git 命令）。

## 1. 🔴 隐私红线（最高优先级）

**仓库、release notes、测试 fixture、代码注释、UI 文案里一律不得出现真实持仓信息。** 具体包括：
真实标的代码（尤其 6 位境内基金代码）、持仓数量/成本、任何金额（含 `123,456.78` 这类千分位写法）、
组合规模与市场构成（"N 个标的 / 各市场几只"）、真实备份片段、个人表格名、构建机绝对路径（`/Users/<用户名>/…`）。

**发布前自查方法**（关键：清单本身不能进仓库，否则等于再泄一次）
```bash
DB="$HOME/Library/Application Support/PortfolioManager/portfolio.db"
# 只取长度 ≥5 的 key：4 位以内的数字会在小数里假命中（如 0.1256 里含 "1256"）
sqlite3 "$DB" "select distinct key from assets where length(key)>=5" > /tmp/pm-tokens.txt
cd "$(git rev-parse --show-toplevel)"    # 仓库根
# 必须排除 .venv（本地依赖，不入库）并跳过二进制，否则噪声会淹没结果
grep -rIlFf /tmp/pm-tokens.txt . --exclude-dir=.git --exclude-dir=.build \
  --exclude-dir=dist --exclude-dir=tmp --exclude-dir=.venv --exclude-dir=__pycache__
grep -rInE '/Users/[a-zA-Z0-9_.-]+/|[0-9]{2,3},[0-9]{3}' . --exclude-dir=.git \
  --exclude-dir=.build --exclude-dir=dist --exclude-dir=tmp --exclude-dir=.venv --exclude=build_app.sh
rm -f /tmp/pm-tokens.txt
```
- 第二条（类别）必须**零命中**；`build_app.sh` 里的 `/Users/<user>` 是清理逻辑本身，已排除。
  个人表格名、自家命名这类正则覆盖不到的，人工再过一遍。
  （预期例外：**本文件自身**的 `/Users/<用户名>` 占位符与 `123,456.78` 示例是规则内容，不是泄露。）
- 第一条：候选资产池文件会有命中，属**预期**：`Sources/PortfolioCore/DataSources/AssetCatalog.swift`、
  `Optimizer/scripts/params.py`、`Optimizer/scripts/market_data.py`、`Optimizer/data/calibrated_params.json`
  —— 这四个文件装的是**候选**资产全集（不是持仓清单），命中项经人工确认「只是候选池内容」后放行。
  **除此之外的任何文件出现命中，一律按隐私泄露处理。**
- 第二类预期命中：**通用公开行情代码**（`BTC-USD`、`0700.HK`、`AAPL`、`000001` 这类谁都写得出的例子）出现在
  测试 fixture、UI 输入提示、文档注释里。判定标准只有一条 —— **读者能否据此推断真实持仓**：不能就放行；
  一旦它与数量/成本/金额/组合构成出现在同一处上下文里，仍按泄露处理。
  （实测 2026-10-09：这条检查会稳定命中 `Tests/PortfolioCoreTests/AssetMarketTests.swift`、
  `Sources/PortfolioManager/Views/AssetEditorViews.swift`、`Sources/PortfolioCore/DataSources/YahooFinanceSource.swift`，
  三处均为上述通用示例，人工核对后放行；每次发布仍需逐条看一眼命中行。）
- 该命令只取长度 ≥5 的 key，3–4 字符的短代码查不出来；这类短代码多为通用代号，
  仍需人工确认仓库里没有把它们与持仓/数量/金额写在一起。
- release notes 也要单独扫（它在 GitHub 上，不在仓库里）：`gh release view <tag> --json body --jq .body` 后同样 grep。
- **示例数值也必须虚构。** 最容易漏的一处是「拿真实数字当例子说明格式化 bug」—— 真实历史里就这么漏过一次
  （release notes 里写了两个真实金额作为 `%g` 舍入的对照）。扫 notes 时那条类别 grep 一旦命中千分位写法，就得改。
- 测试一律用**虚构 fixture**；季度指标的期望值不要手抄，用 `QuarterlyMetrics.compute` 跑出来再固化。
  （2026-10-14 实测：单测 fixture 里写了一个真实基金代码当"数据来源"字段，被自查脚本的
  白名单检查抓出 → 自那以后**测试目录不再豁免**，两个模式一视同仁，fixture 必须虚构。）

- **已自动化**：`scripts/privacy_check.sh`（`--staged` / `--message` / `--install`）；
  `--install` 会写好 `.git/hooks/pre-commit`（扫暂存区）与 `commit-msg`（扫提交信息），
  本地黑名单 `tmp/privacy-numbers.txt`（gitignore 内、一行一个正则）专门挡"本地跑出来的组合级数字"。
  判定分级：候选池文件直接跳过；通用公开代码（三处 Swift 与 AGENTS.md 自身）打印出来供人工确认；
  其余命中一律按泄露处理并让提交失败。
- **🔴 实测数字一律不入库**：优化权重、组合收益/波动/夏普、真实池拆分比例、内部标的权重……
  这些都不是"候选池内容"，写进文档/注释/提交信息即等于泄露组合结构。**只写机制与教训**；
  本地验证记录留在 `tmp/`（gitignore）或工作区外。

### 历史教训（为什么上面这些规则这么硬）
- 本项目真出过一次隐私泄露，**泄露渠道全部是「人写的文本」**：release notes、测试 fixture、代码注释、UI 文案。
  DMG 里从来没有数据库/金额 —— 所以「打包产物干净」不等于「没泄露」。
- 处置动作：脱敏并重发 notes、fixture 全部改虚构、注释/UI/CLI 改中性示例、脚本里的构建机路径改环境变量、
  重打发布包，并**改写全部历史 + 重建仓库**（因此旧 SHA 现已取不回，见 §2）。
- 一句话：这类事故的代价是整个仓库重做，所以发布前那两条 grep 必须跑。

## 2. 仓库状态与历史（会影响你看到的 SHA）
- remote `origin https://github.com/xiang9916/PortfolioManager-app.git`，分支 `main`。
- **重建于 2026-09-30**：所有提交 SHA 都变了，更早文档/对话里的 SHA 不再对应，`git fetch origin <旧SHA>` 会失败（这是想要的效果）。
- 线上 14 个 release（v0.1-beta2 … v0.4-beta3）已逐一还原：标题、pre-release 标记、脱敏 notes、DMG —— 按 GitHub asset `digest` 逐个核对与本地归档**字节一致**。
- 本地分支 `backup/beta3-beta4-work`（0.3-beta3/4 时期的备份线，比 main 多 4 个提交）**从未推送过**；要继续用就 `git push -u origin backup/beta3-beta4-work`。
- 无 CI workflow：DMG 本地构建 + 手动 `gh release create` 上传。

## 3. 恢复套件（在仓库内 `tmp/`，已被 .gitignore 忽略）
- `tmp/pm-repo-20260930.bundle` —— 全 refs 备份（main + 14 tags），实测 `git clone <bundle>` 可还原。
- `tmp/release-archive/` —— 脱敏 notes（重建的 13 份 + 之后各版）、13 份 release 元数据、`SHA256SUMS`、`tags.txt`、`repo-settings.json`，
  以及两个脚本：`redownload_dmgs.sh`（拉回 13 个 DMG 并校验）、`rebuild_repo.sh`（重建仓库 + 13 个 release，已跑通）。
- ⚠️ **不要依赖 `/tmp`**：同类归档放在 `/tmp` 曾被系统清理过一次。

## 4. 构建与预览（★ 有坑）
```bash
swift build --disable-sandbox          # DSH 沙箱下必须带 --disable-sandbox
swift test  --disable-sandbox          # 87 个用例；2 个联网用例按设计跳过（PM_LIVE_TESTS=1 才真跑）
bash scripts/build_dev.sh [--run]      # ★ 预览/截图一律走这个，别直接 swift build
```
- **为什么不能直接 `swift build`**：SwiftPM 会把 deployment target 当成 SDK 版本写进 `LC_BUILD_VERSION`（实测 `minos 14.0 / sdk 14.0`），
  macOS 据此判定「用旧 SDK 构建」并启用**兼容外观**（TabView 标签栏落到标题下方、窗口标题常显）——
  和用户实际装到的样子不是一回事，截图核对会失效。`build_dev.sh` 显式传
  `-Xlinker -platform_version -Xlinker macos -Xlinker 14.0 -Xlinker <真实SDK>`，并把实际 minos/sdk 打印出来供核对。
- 真实 SDK 版本 = `xcrun --sdk macosx --show-sdk-version`（本机 Xcode 27.0 → `27.0`）。
- 开发库 `<cwd>/tmp/portfolio.db`；真实库 `~/Library/Application Support/PortfolioManager/portfolio.db`（**勿做破坏性验证，先复制**）。

## 5. 打包发布
```bash
bash scripts/build_app.sh --with-venv --dmg     # 必须 --with-venv，否则优化器没有 Python
```
- **发布产物 = 仅 DMG**（自 **0.6-beta.4** 起不再提供 ZIP）：`dist/PortfolioManager-<版本>.dmg`
  （内含 app + `/Applications` 快捷方式，拖拽安装）。`--dmg` 只产出 DMG；`--zip` 仅保留给本地调试，
  **不再随发布上传**（release 只挂 DMG 一个 asset）。
- **DMG 里不再放「一键安装.command」**（自 0.4-beta3 起）：它自身同样带隔离标记、双击第一次照样被 Gatekeeper
  拦住，收益为零却要多维护一个脚本。仓库里的 `scripts/install_dmg.sh` 保留，供有仓库的人从终端安装。
- **内置隐私加固（勿删）**：① venv 拷完后清 `__pycache__` 并把 `/Users/<user>/` 重写成 `/build`；
  ② 二进制 debug info 里的 `/Users/<user>` 在 codesign **之前**做等长替换成 `/tmp/pmbuild`。
  **③ scripts 树下 `__pycache__` 必须递归清理（2026-10-17；ADR 0003 起 `Optimizer/scripts` 下有了子包 `portfolio_system/`）**：
  `.pyc` 头部会原样写入源码**绝对路径**，旧写法只删顶层那一层 → 实测会把 `/Users/<user>/…/portfolio_system/core/linalg.py` 打进 bundle。
  **④ 打包末尾的 bundle 隐私硬门（codesign 之前）**：包内出现任何 `__pycache__`/`*.pyc`、或出现**本机 HOME 前缀**，一律让打包 fail；
  第三方 wheel 的 `.so` 里带**上游 builder** 的 `/Users/<name>/`（实测 2 个 cargo registry 路径，不含本人任何信息）只统计打印、不阻断。
  副作用：DMG 体积 85MB → 48MB（少了 .pyc），首次跑优化器会重新编译缓存。
- 版本 bump：只改 `scripts/Info.plist`（`CFBundleShortVersionString` + `CFBundleVersion` 同步）→
  commit `chore: bump version to X.Y` → annotated tag `vX.Y`（message `PortfolioManager X.Y`）
  → `gh release create --prerelease`，notes 中文 + **DMG 的 SHA256 行** + 只挂 DMG 一个 asset（不再传 zip）。
- 上传后必须核对：`gh api repos/xiang9916/PortfolioManager-app/releases/tags/<tag> --jq '.assets[] | .name, .digest'`
  与本地 `shasum -a 256` 一致，且 notes 里的 SHA 行同步更新。
  ⚠️ 别加 `--deep --strict`：venv 里 `bin/python3` 是指向本机 conda 解释器的**绝对符号链接**，
  strict 模式必然报 `invalid destination for symbolic link in bundle` 并退 1 —— 这是既有现象（0.4-beta3 的产物同样如此），
  不是这次打包坏了；同理 DMG 挂载后做 strict 校验也会报 `file modified`（HFS+ 转换改写符号链接元数据）。
  想根治只能把 venv 建成 `--copies` 的自包含形态（会显著变大），目前不做。
- `hdiutil create` 在沙箱下会失败 → `build_app.sh` 内置 `makehybrid` + `convert` 回退；镜像固定 HFS+（APFS 会大约 50%）。

## 6. 架构与数据模型（Schema v8 现状）
```
PortfolioManager (SwiftUI)  ──依赖──▶  PortfolioCore (纯逻辑, 无UI)  ──▶  Optimizer/ (Python venv 子进程)
  ContentView: 3 Tab               ├ Models (Asset/Holding/Snapshot/QuarterlyReport/AssetMarket…)
  AssetOverview/AssetPerspective/  ├ Database (SQLite + Schema v8 迁移 + 幂等数据归一)
  FinancialAnalysis 视图            ├ Query (Repository 聚合 + QuarterlyMetrics 公式链 + QuarterlyFill)
  AppStore = @MainActor 状态中枢    ├ DataSources (Yahoo/Eastmoney + AssetCatalog)
   持有 db / repository / optimizer ├ Backup (每日 .db 快照 + JSON 导入导出)
                                   ├ Optimizer (OptimizationService → PythonSidecar)
                                   └ Import/JSON (PortfolioImporter / ResultImporter)
pm-cli (headless CLI) 复用 PortfolioCore
```
红线：**`PortfolioCore` 永不 import `PortfolioManager`**；Views 只经 AppStore 取数。
- 市值 = 份额 × 最新价（派生不落库）；`quotes` = 最新价，`prices` = 历史 K 线。
- 财务分析 = `quarterly_reports`（一列一季，9 个手动字段，其余由 `QuarterlyMetrics.compute` 派生）。
- **市场**是闭集 `AssetMarket {US, HK, CN, JP, SG}`：按 ticker 推断（`.HK`→香港、`.T`→日本、`.SI`→新加坡、
  6 位数字/`.CN_FUND`→中国内地、纯字母→美国），也可手选；旧自由文本（`NASDAQ`/`HKEX`…）归一到这五个，认不出的置空。
- **池（境内/境外）由市场派生、读路径现算**；`assets.pool` 只是写入时的缓存；`Pool.cross`（跨池）已彻底删除。
- `Database.migrate()` 末尾调用 `normalizeAssetClassification()`（幂等数据归一，不占 Schema 版本号）。
- 命名：模块1 **资产总览**、模块2 **资产明细**、财务分析；明细里是 **成本 (折人民币) / 成本 (CCY)**；
  聚合口径叫 **总成本**，卡片第一项叫 **总市值**。术语表见 `CONTEXT.md`，决策见 `docs/adr/0001`、`0002`。
- 财务分析「一键填入」走 Core 的 `QuarterlyFill`（可单测）：只填**当下**总市值/总成本；期初基准列两格同填总成本。
- **弹窗（sheet）一律给固定尺寸 + 内部滚动**：只写 `minHeight` 的话，sheet 会按内容长度一路长到屏幕之外
  （结果页标的数、提示条数都会把它撑长，2026-10-14 实测）。做法见 `OptimizationResultView.panelSize`
  （屏幕可见高度 − 余量 + 顶部固定栏 + 底部关闭出口），长列表另配可折叠区块。
- 股息税率锚定同一个市场结论（`DividendTax.rate(market:)`），JP/SG 未配置 → nil。

## 7. 测试
- `swift test --disable-sandbox` → **87 个用例全绿**（其中 2 个联网用例按设计跳过，需 `PM_LIVE_TESTS=1`）。
- fixture 一律虚构；季度指标期望值由 compute 生成后固化。**改版前先跑全量测试。**

## 8. 已知限制（v0.4-beta2 已对外声明）
- 一键填入只填当下值，不是该季末历史值（`snapshots` 表建库至今没有任何代码写入过）。
- 前年无分红的标的外推偏高（公式 `max(0, 2×去年 − 前年)`）。
- 当年新上市的标的显示 `¥0.00` 而非 `—`。
- 日本/新加坡股息税率未配置（按 0% 计，详情标注「未配置」）。
- 缺汇率的币种按 1:1 估算市值与成本。
- **有意保留**：`AssetCatalog.swift` / `Optimizer/scripts/params.py` / `calibrated_params.json` 里是候选资产全集
  —— 它是**候选池、不是持仓清单**（持仓只存在于本地数据库，不入库、不进 DMG）；删了会让「刷新行情」失效。
  若将来要中性化，得先把 symbol 落库（改数据模型 + 迁移）。

## 9. 环境与验证手段（本机实测）
- 屏幕录制权限**已授予**：`screencapture -x -o -l <windowID>` 可截窗口（windowID 用 `CGWindowListCopyWindowInfo` 按 PID 过滤）。
- 辅助功能/合成点击**不可用**：`CGEvent` 点击会被静默忽略，`osascript` System Events 会卡住 →
  **不要指望脚本自动点按钮做验证**。稳妥做法：把逻辑抽到 PortfolioCore 写单测 + 用截图核对静态界面。
- 需要「截图里选中某单元格」时，只能临时加 `.onAppear` 选中钩子；**用完必须删掉**并按 `TEMP-VERIFY` 自检（勿留在仓库里）。

## 10. Gatekeeper / 隔离标记
- app 只有 **ad-hoc 签名**（`codesign --sign -`），无开发者证书 → 从网络下载的 DMG 会触发「Apple 无法验证」；
  彻底消除只能靠 Apple Developer Program（$99/年）Developer ID 签名 + 公证。
- **安装方式**：打开 DMG 把 app 拖进「应用程序」，首次打开在
  「系统设置 → 隐私与安全性」放行一次即可，之后正常。**DMG 内不再有一键安装脚本**（见 §5）。
- `scripts/install_dmg.sh`：给有仓库的人用的一键安装（拷到 /Applications + `xattr -dr com.apple.quarantine`）——
  终端 `bash scripts/install_dmg.sh [dmg]`，不带参数时自动挑 `dist/` 里最新的 DMG。

## 11. 备份/恢复语义
- `BackupManager.exportJSON`：导出 assets/holdings/snapshots/quarterly_reports/income_periods/fx_rates/quotes + schema_version。
- `importJSON(from:clearAssets:clearFinancials:)`：默认**合并**（upsert）；`clearAssets` 先清
  holdings/prices/quotes/snapshots/assets/fx_rates；`clearFinancials` 先清 quarterly_reports/income_periods。
  UI 导入前弹窗让用户勾选；pm-cli 对应 `--clear-assets` / `--clear-financials`。
- 财务字段 UI 防抖 0.6s 落盘；**导出备份前记得 `flushQuarterlyPersist()`**（AppStore 已处理）。
- 已删除功能：PDF 导出（按钮/AppStore/PDFExporter/pm-cli report 全移除，勿复活）。

## 12. 优化器（能力2）注意
- Python 解释器解析链：`PORTFOLIO_OPTIMIZER_PYTHON` → App bundle `Resources/Optimizer/.venv` → 仓库 `Optimizer/.venv` → PATH。
- 外部数据目录：`DSH_FINANCE_DIR`（默认 `~/Finance/tmp`）。
  **不要写死 `/Users/<某人>/…` 路径**（构建机绝对路径是隐私泄露源，见 §1）。
- venv 解释器 `/opt/anaconda3/bin/python3`（实测 3.14.6；`/opt/miniconda3` 在本机已不存在，别照旧路径找）。
  bundle 里 `Resources/Optimizer/.venv/bin/python3` 是指向它的**绝对符号链接**（strict 校验的坑见 §5）。
- futu-api 已从 `Optimizer/requirements.txt` 移除（代码中无 import；它会带入约 30MB 的 pycryptodome 等依赖）；行情走 Yahoo/Eastmoney 公开源。仓库不得出现任何 API key/凭据。
- 「新标的测试」= 临时加 ticker 重跑，不改持仓，结果在弹窗（testOptimization 状态）。
- **🔴 参数口径（2026-10-14 定稿，改前务必读 `Optimizer/scripts/asset_classes.py` 的 docstring）**：
  预期收益 μ / 波动 σ 按**资产类别**分别构造，类别与规则**只有一份事实来源** = `asset_classes.py`：
  | 类别 | μ 构造 | μ 截断 | 历史窗口 | σ |
  |---|---|---|---|---|
  | broad 宽基/大类（宽基被动、债券、货币、黄金、商品、大中华 INDEX 桶） | `0.5×历史 + 0.5×（本币RF+溢价）` | `0~20%` | 全历史 | 历史年化波动 |
  | strategy 策略指数（红利低波/价值/动量/高股息/主题/REIT/缓冲期权） | `0.67×历史 + 0.33×（本币RF+溢价）` | `−5~30%` | 20 年 | 历史年化波动 |
  | active 个股/主动基金 | `1.00×历史`（不用先验） | `−10~40%` | 10 年 | 历史年化波动 |
  特例：`O_BTC` 截断 `−20~60%`；`D_HSBC_MMF` 写死 μ=2%/σ=1%。
  窗口取不到那么长就用可得全历史；`σ > 150%` 视为数据毛刺 → 丢弃并报警；**0.6 的 σ 上限已取消**。
  **σ 的人工下限也已整体取消**（2026-10-14）：历史波动多低就是多低，不再 `max(历史, 下限)` ——
  人工下限会系统性把「真实低波资产」抬成「中等波资产」，让结果偏离真实风险。样本太短的问题改用
  长历史代理解决（`market_data.PROXY_MAP`）。代码里**不再有 `VOL_FLOOR` 表**。
- **先验 = 本币无风险利率 + 溢价**（2026-10-13 定稿；🔴 **利率必须是数据，不能写死在程序里**）：
  `prior = rf(币种) + RF_PREMIUM[key]`。溢价写死但**统一 0.5% 粒度**；币种按标的**实际敞口**判定
  （境内 A 股/债券基金 = cny，其余含港币与美元资产 = usd；标普500 QDII、美元债 QDII 按 usd）。
  RF **只来自数据源**（东财「中美国债收益率」：美元 10 年期 / 中国 10 年期），校准阶段取到后
  经 `asset_classes.set_rf()` 注入，并写进校准 JSON 的 `prior_rf`；运行时 `params.py` 再注入 →
  离线可复现。RF 取不到则回落到 `PRIOR` 兜底表（同样 0.5% 粒度）并报警。**代码里不得出现利率常量。**
- **债券大类的先验是动态公式，不是写死数值**（2026-10-13 用户定稿）：
  `先验 =（回归 μ + 当期同久期国债收益率）/2`，其中 `回归 μ = a×当期 10 年期国债收益率 + b` ——
  回归来自「该资产持有 5 年的年化收益 y ~ 同币种 10 年期国债收益率 x」，t 取距现在 > 5 年的所有时点
  （**不预设斜率符号**：利率高时买债后续收益更高还是更低由数据决定）。锚定期限与久期匹配：
  `D_HSBC_CDB` 用中国 5 年期、`O_US_TLT` 用美国 20 年期。规格在 `asset_classes.BOND_PRIOR_FORMULA`
  （各自的静态 `PRIOR` 值降级为"公式不可用时的兜底"），回归/锚点由 `Optimizer/scripts/bond_prior.py` 计算
  （`--json` 出报告；也可单跑看诊断），校准阶段算好写进 JSON 的 `priors`/`bond_anchors`，
  `params.py` 加载时注入，**离线也能复现**。实测 2026-10-13：CDB 3.50% → 1.84%（μ 3.44% → 2.61%）、
  TLT 4.00% → 8.91%（μ 1.79% → 4.24%）。组合层面的权重变化属本地验证记录，不入库。
  三个坑：① 回归**必须用复权/累计净值**序列（债券 ETF 的价格序列把票息丢掉，实测把 TLT 的
  a/R² 从 0.60/3.64 带成 0.53/3.36）；② 东财那张表**没有美国 20 年档**（FRED 本机不可达）→
  20 年期用 10Y/30Y 线性插值合成并如实标注；③ 自变量固定是 10 年期，代入时别错用锚定期限。
  其余债券（双债增强 R²=0.00、QDII 稳健样本 0、HYLB 非重叠仅 6 个）**不适合公式化**，
  仍用静态先验，`bond_prior.py` 会给出诊断理由。
- **兜底矩阵（数据缺失时）**：有写死+有行情→按类别混合；**有写死+无行情→用写死值**（目前只有货币基金这种 μ/σ 全写死的标的）；
  **无写死+有行情→纯历史并报警**（口径与在册标的不可横比）；**无写死+无行情→丢弃并报警**
  （σ 没有下限可退，宁可不参与也不编造风险度量）。
  `params.py` 里**不再保存任何静态 μ/σ**（表格只剩名称/代码），加载时按"校准值 → 类别兜底"填充；
  每个数的来历记在 `PARAM_SOURCE` / 校准文件的 `provenance`，可用 `params.describe_param_source(key)` 打印。
- **active 类单标的组合权重上限 10%**（`asset_classes.ACTIVE_CAP`）：stage-1 上界、美股内部（stage-2）、
  日本/香港/大中华桶优化都要套；与境内池权重冲突时**自动放宽并报警**（否则"境内只有一只可用标的 + 要求 30%"会直接不可行）。
  触顶时合并成一条提示（"上限 10% 生效：<标的> 10.00%"）。上限表**必须覆盖内部标的**，只从大类槽位构建会漏掉美股内部标的（如 AAPL）。
- **🔴 求解入口只有一个：`optimize_portfolio.solve_portfolio()`**（2026-10-14 起）。它 = 阶段一（大类）
  + **袖内固定点迭代** + 阶段二（袖内再优化）。结果页（`optimize_portfolio.py`）与敏感性分析
  （`sensitivity_analysis.py`）都调它，**不允许再各写一套** —— "结果页与敏感性图上同一个目标点给出
  互相矛盾的数字"就是这么来的（敏感性只扫了阶段一）。
- **袖内参数固定点（2026-10-14 修的关键 bug）**：所谓"袖"= 由内部标的动态组成的大类：
  `O_US_CORE`（美股核心）、`O_JP_EQ`（日本权益）、`D_GREATER_CN`（大中华权益）。
  阶段一过去把袖建模成"**当前持仓那一篮子**"（`us_core_params` 按 live 权重聚合 μ/σ），
  而阶段二会在袖内**重新选标的** → 阶段一的模型 ≠ 交付组合：目标落在切点收益以下时，阶段一正好
  贴着目标交付，阶段二却把交付收益顶出目标一大截，敏感性分析（只扫阶段一）自然与结果页对不上。
  修法：迭代袖内 (μ, σ) 到固定点（阻尼 0.5、最多 10 轮、判据 = 每只袖的 μ/σ 变化 ≤ 0.4pp），
  于是**阶段一假设的袖 = 阶段二交付的袖**，目标约束对交付组合有效（实测模型/交付差 < 0.5pp）。
  ⚠️ 袖内选标是**离散**的（谁入选、谁触 10% 上限会跳变），某些参数区（目标远低于切点、live 篮子又很单一时）
  映射会形成"极限环"、没有真正的固定点 —— 这时实现取**模型 vs 交付误差最小**的那一轮，并在提示里写明
  "未完全收敛"，不硬性采用最后一轮。2026-10-17 起择优序改为「**交付是否达标 → 交付缺口 → 模型与交付的差 → 轮次号**」（旧序会把**没达标**的一轮顶上来当选），并新增加法式 `sleeve_cycle`（振幅）供事后定位；**仍不承诺无条件收敛**（袖内选标离散，结构上无法证明）。审计字段见输出里的 `sleeve`（params / iterations / converged / history）。
- **🔴「袖全空」不得判定收敛（2026-10-15 修的关键 bug）**：目标高于"当前持仓篮子模型"能支撑的水平时，
  第一轮 `_solve_stage1` 会判定目标不可行并**回退到最大可行收益**，而回退解是"各池全押最高收益标的"的
  角点组合 —— 它通常不给袖任何权重，于是阶段二整段跳过（袖权重 ≤1e-6 即早退）、袖参数"没有变化"、
  `delta=0` 被误判成收敛，**收益低于用户设定目标的角点组合就这样被交付了**。
  ⚠️ 该 bug 极其隐蔽：只要角点恰好残留**一丝**袖权重（>1e-6），阶段二就会跑、delta 非 0、循环就能继续，
  几轮后袖 μ 被迭代上来、目标重新可行 —— 交付结果一度取决于那一丝权重是否恰好为零（用户实测：
  **加一个新标的**把那一丝抹平，结果从正常前沿解塌成角点）。
  修法（`solve_portfolio`）：① `fell_back = solved["target"] < target - 1e-9` 的回退轮**不参与收敛判定**，
  也不参与"最优轮"（`best`）挑选；② 回退轮里**没被解出**的袖（`_resolved_sleeves` 判空袖）按它自己的
  候选池重新播种为"它自己能达到的最优篮子"（`_sleeve_reseed_params`，不设单标的上限）后再迭代一轮；
  ③ 袖都被解出时照常阻尼推进；④ 袖全空且已在播种值上 → 停止，并在提示里**显式**写明"目标在收敛后的
  模型下仍不可行、已按最大可行收益 X% 交付（低于目标）"；若是因为**迭代预算用尽**而停下（末轮可能
  只是"回退 + 袖被解出"、阻尼推进还没被检验），提示改说"结论受迭代预算限制、可提高预算后重试"，
  不误报"收敛后的模型下不可行"、也不建议下调目标。
  ⑤ 同类漏洞一并堵住：**"阶段一给了某只袖权重、阶段二却没跑出结果"的轮次也不算收敛** ——
  那种轮次的 `final` 只是阶段一的模型统计（`model_gap` 恒为 0，会伪装成"模型与交付完全一致"）；
  评分因此改为 `(交付是否真的发生, 模型与交付的差, 是否收敛)`。
  ⑥ 重播种后那一轮会额外获得预算，否则 `max_sleeve_iter` 刚好用尽时会把已经算出的
  重播种丢掉、还会把**可达**的目标误报成"不可行、请下调目标"（上界固定为 `iters + max(1, 袖数)`，
  保证总轮数有界）；`sleeve_params` 输出选中轮**真正用过**的参数（不是从未被求解过的重播种值）。
  回归测试：`Optimizer/tests/test_sleeve_empty.py`（8 项；受控桩确定性复现"回退轮 + 三袖全空"，
  并覆盖"预算=1 时重播种必须有下一轮""交付没发生的轮次不得算收敛""预算用尽不得归因到目标"；
  在修复前的代码上 7/8 失败）。
  `history` 每轮新增 `fallback` / `reseeded` 字段供事后定位。
  **仍然存在（非本次引入）**：袖内池为空 / 全部起点失败时阶段二给不出篮子，`final` 会退化为阶段一的
  **模型**统计（active 10% 上限口径下可能与真实可交付收益不一致）。2026-10-16 起：这种轮次会在交付结果里**显式标注该袖不可交付**（点名是哪只袖 + 原因：候选池为空 / 全部起点失败，并声明 `final` 只是阶段一的模型统计、请勿据此执行）。那是 2026-10-16 的边界（报警 + 不算收敛）。**2026-10-17 起改为真正处理**：先在组合层**按 0 权重剔除该袖后重新求解**（真有解的路径，告警码 `sleeve.excluded_no_basket`，加法式键 `excluded_sleeves` / `weighted_no_basket`）；剔除后仍不成才返回 `deliverable=false` + `final_source=stage1_model` + 告警码 `sleeve.undeliverable`，**显式声明不可交付**，不再把模型统计当交付。⚠️ 语义澄清：`deliverable` 只表示「这条结果的**统计来源**」，不是「达标」—— 达标看交付收益与目标之差。回归测试：`Optimizer/tests/test_sleeve_robustness.py`（5 项；修复前祖先上 0/5，全是行为断言）。
- **两处内部 API 崩溃点已改为可诊断 / 安全降级（2026-10-16）**：`stage2_optimize` 缺 `O_US_CORE` 不再抛裸
  `ValueError`（改为返回「不可用 + 实际大类」的可诊断原因并进 `warnings`）；`sleeve_start` 的 σ≤0/NaN
  在入口就抛含 key / 取值 / 来源的 `ValueError`（此前会一路走到 `project_psd` 抛 `LinAlgError`，负 σ 甚至静默通过）。
  回归测试：`Optimizer/tests/test_solver_guards.py`（5 项）。
- **回退提示不再逐轮累积（2026-10-16）**：目标不可行时，逐轮回退提示过去会一直攒到迭代上限
  （`max_sleeve_iter + max(1, 袖数)` 轮，合并前最多约 14–15 条），现按标记合并去重为**一条**，
  保留三个信息点：① 交付收益低于目标 ② 最大可行收益 ③ 迭代预算是否用尽。
  回归测试：`Optimizer/tests/test_fallback_prompt.py`（4 项）。
- **袖内目标传导（2026-10-16，方案 A 第一期）**：此前只有 `O_US_CORE` 的袖内优化带组合级收益约束，
  `O_JP_EQ` / `D_GREATER_CN` 只做袖内 max 夏普、**看不到组合级目标** → 目标高于切点时这两只袖原样不动、
  甚至被整体丢弃、组合退成角点。现在为它们求一个**统一的 μ 下限 `t_s`**：由「阶段一上界 + 袖内
  **无上限**可达最大收益」二分得到（与袖权重无关，因此不随 `cap_norm` 抖动，也天然协调多只袖），
  袖内求解带 `x·μ ≥ t_s − ε`，**循环后再断言一次**；任一道不过就返回「不可行」并进具名 `warnings`
  （历史教训：袖内求解器只校验 `Σx=1` 与 bounds、**从不校验不等式可行性**，单标的池会返回既不满足下限、
  也不报不可行的篮子 —— 静默违约就是这么来的）。目标超过袖内可达上限时**故意不为该袖建下限**、显式提示 —— 2026-10-17 起这只是**袖级**事实，不再把整轮判成组合级不可行（见「目标可达性只在组合层判定」条）。
  「模型可达上限」= 本轮生效目标；新增的收敛判据与 `delivered_ok` **只在非回退轮**且本轮确有下限时启用
  （否则不可行路径永不收敛）。新增输出键（**加法式**，仅在下限生效时出现）：
  `sleeve_history[i].sleeve_floors` / `.floor_recomputed{required_mu,tangency_mu,uncapped_max}` / `.floor_seeded`、
  `japan_selection`·`gc_selection` 的 `.floor` / `.floor_met`（低目标档一个键都不变 → 正常路径逐字节一致）。
  回归测试：`Optimizer/tests/test_sleeve_target.py`（5 项）。**性质不变量**：`t_s` 低于袖内切点收益时
  输出与改动前逐字节相同（多档低目标以 `cmp` 守住）。未采用：跨层协方差（B，反馈更强、极限环概率更高）
  与「λ 袖前沿联合求解」（C，需重构阶段一、把风险移进非凸求解器）。**已知残留（已于 2026-10-17 单源化，见本行末条）**：`ε` 曾在三处叠加
  （`t−ε` / `floor−ε` / `target−ε`）→ 实际保证约为「需求 − 3ε」（`SLEEVE_FLOOR_EPS`）；两道闸同谓词同 ε、
  后者结构上不可达；截断用无 cap 上界而交付用带 cap 上界（JP/GC 当前无 active 类标的）；
  `_floor_seed` 返回 None 时不播种且不告警。
- **ε 单源化（2026-10-17）**：交付保证只用**一次** ε = `DELIVERY_EPS`（`SLEEVE_FLOOR_EPS` 保留为它的**无字面量别名**），袖内下限就是需求本身、袖内求解器只留 `_SOLVER_EPS=1e-7`（纯数值）→ 实际保证 =「交付收益 ≥ 目标 − ε」，**不再是「需求 − 3ε」**。语义不同的容差各自命名：余量筛选 `SLEEVE_ROOM_EPS`、数值自检 `CHECK_SUM_EPS`；**决策点不得出现裸字面量**（`grep -n '1e-4'` 应只剩常量定义）。回归测试：`test_sleeve_robustness.py`。
- **回退角点清零袖后的播种水平（2026-10-16，修的是上一条的残留）**：目标高于"袖按切点口径"的可达上限时，
  回退角点常把**三只袖全部压到 0 权重**；旧逻辑只按"该袖自己的**切点**篮子"重新播种，而切点 μ 可能明显
  低于阶段二实际能交付的篮子 → 阶段二因 0 权重早退、**永不运行** → 模型永远够不到目标 → 直接宣告
  "目标确实不可行"并交付高波动角点。真实数据上的表现：主运行正常，**注入一个测试标的就塌**
  （角点、交付低于目标、提示"目标不可行"）。修法（attempt#2）：对"**前沿尚有余量**"（可达上限明显高于
  切点 μ）的袖，按"**刚好够到目标**的最小水平"播种 —— 逐袖求水平（不被可达上限最低的袖卡死）、
  只动有余量的袖（无余量的袖完全不碰）、播种后**强制多给一轮预算**（否则播种等于白播）。
  触发条件严格限定在"目标高于切点前沿 **且** 本轮报出「袖内收益下限无法建立」"的回退轮；
  低目标档一个字节不变（性质不变量 cmp 守住）。审计键 `history[i].sleeve_seeded_levels` 为**加法式**、
  仅触发时出现。
  ⚠️ **教训（attempt#1 已回退）**：播种量必须由"组合需要多少"决定，**不能取该袖自己的上限** ——
  按袖上限播种等于把一个过冲源塞进固定点迭代：模型里的袖 μ 跳到上限、与阶段二交付篮子来回摆，
  **连本来正常的主场景也塌成多轮回退角点**。播种是"给阶段二一个上场机会"，不是"把袖抬到最高"。
  回归测试：`Optimizer/tests/test_sleeve_corner.py`（6 项；受控桩确定性复现"回退角点 + 三袖 0 权重"，
  在真·修复前的祖先上失败原因**全是行为断言**）。提示文案断言刻意不放在这个文件里（关注点分离：
  由 `test_fallback_prompt.py` 负责），于是祖先对照只反映行为差异。
- **🔴 目标可达性只在组合层判定（2026-10-17 修的关键 bug）**：阶段一曾用「当前假设的袖 μ」判可达性，并把结果当成**组合级**结论 —— 只要某只袖的假设 μ 偏低（固定点尚未收敛）或该袖自身够不到目标，就整轮标成回退轮：目标被降低、阶段二失去目标约束、交付塌成角点，结果页并报「袖内收益下限无法建立……本轮走既有的回退/不可行路径」。真实数据上的表现：注入一个新标的（active 类、受 10% 上限约束）后它**从交付组合里消失**，而组合目标其实靠别的池就能达成 —— 用户看到的正是那句提示。修法：逐袖求「组合刚好够到目标」所需水平（`_sleeve_levels_for_feasibility`），袖至少按自己的切点篮子参与、没余量的袖按自身上限参与（**不被可达上限最低的袖卡死**），全抬满仍够不到才是真·组合级不可行；收尾只在**最终交付确实没到目标**时才按请求目标重解一次（阶段二照常带目标约束）。低目标档的**决策字段**一个字节不变（`cmp` 守住；`warnings` 自 2026-10-17 起按**选中轮**刷新，可能与更早版本不同）。回归测试：`Optimizer/tests/test_sleeve_no_room.py`（5 项，受控桩 + 虚构池；修复前祖先上 2/5，失败全是行为断言）。
- **告警改成可机读 code，且 summary 必须接线（2026-10-17）**：`warnings`（字符串数组）保持**字节不变**，加法式新增 `warning_items: [{code, message}]`，code 表 `WARNING_CODES`（19 个，如 `target.infeasible` / `sleeve.undeliverable` / `active_cap.hit` / `summary.weight_sum_mismatch`）。**关键：summary 与 `--json` 两条路径都要带** —— App 读的是 `build_result_summary()` 的 summary，它原先没有这个键，于是生产上等于没接线（Swift 只能退回文案匹配）；生成必须以**最终** `summary_warnings` 为准（`CAP_HITS` 与 summary 自检类是在后面才 append 的）。Swift 侧 `PortfolioCore/Optimizer/OptimizationWarning.swift` 建 19 个 case + severity 映射（单一来源、有单测），新结果**只认 code**、旧结果文件（无该键）走文案兜底；View 不再自留关键词表。回归测试：`Optimizer/tests/test_summary_warning_items.py`（5 项）+ `Tests/PortfolioCoreTests/OptimizationWarningTests.swift`。
- **「实际 − 目标」的判定与容差下沉 Core（2026-10-17）**：`OptimizationTargetAssessment`（`tolerance = 1e-4` 唯一来源）输出 `gap` / `met` / `shortfall` / `severity`，View 只消费、不再自带魔法阈值；`severity` 的绝对值分档（none/marginal/material/severe/indeterminate）是**展示口径**，不是业务口径。
- **测「祖先上是否失败」必须走 `PM_OPT_SCRIPTS`（2026-10-17 教训）**：新用例若硬写 `../scripts`，把测试放进祖先树跑会**静默加载仓库当前脚本** → 得到「祖先全绿」的假结论（本轮真的发生过一次，被独立核验抓出）。正确做法：`PM_OPT_SCRIPTS=<祖先>/Optimizer/scripts` **且**把测试文件拷进祖先树；只改 `PYTHONPATH` 同样是假对照。
- **交付的 warnings 只反映「选中轮」（2026-10-17）**：固定点迭代里中间轮的降级告警过去会被一并并入交付结果 → 结果页可能常显一条与终态**自相矛盾**的 critical 提示（例如交付已带阶段二篮子、`final_source='delivered'`，却还挂着「美国权益内部优化不可行，组合统计已回退使用第一阶段结果」）。现在：交付 `warnings` / `warning_items` 只并入**选中轮 + 调用方 + 收尾汇总**一次，中间轮告警移到加法式审计 `history[i].warnings` / `history[i].warning_items`；同时 `deliverable` / `final_source` 加法式进 summary 与 `--json`（此前只在内部）。回归测试：`Optimizer/tests/test_warning_round.py`（4 项；祖先 2/4，含行为断言失败）。
- **告警 code 不许「登记了却发不出来」（2026-10-17）**：`test.ticker_skipped` 与 `fund.available_none` 曾在 `WARNING_CODES` 里登记却从未被发出 → 落到兜底 `other`，结果页对应态在生产路径上恒不出现。已补发（**消息文本逐字未变**，只补机读 code）。回归测试：`Optimizer/tests/test_warning_codes.py`（4 项，「登记 ↔ 发出」双向覆盖；祖先 1/4）。
- **「注入的标的没入选」不等于缺陷（2026-10-17 诊断结论，重要）**：内部标的只有 stage-2 的**袖内池**成员才是候选；一只**顶层大类腿**走的是 stage-1 模型 + active 10% 上限表，**本来就不在袖内池**里。它是否入选**取决于目标档**：同一条腿在较低目标档（收益约束未绷紧）**都入选并正好顶住 10% 上限**，只有高目标档为 0 —— 因为同样的 10% 额度让给收益更高的腿才够得到目标（把它加回去会降夏普）。所以「结果里没有它」是**正确结果**，不是漏选/丢数据；只有结果带 `test.ticker_skipped` 才是数据不可用。结果页新增「测试标的结论」区把三态（已入选/触上限、本轮未分配权重、因数据不可用被跳过）**显式区分**（纯 UI：不改 JSON 契约、不硬编码上限值、不做未经证实的归因）。
- **敏感性分析 = 完整流水线逐点扫描**（2026-10-14 起）：每个目标点都跑 `solve_portfolio`（用上一点的袖参数
  热启动），于是图上每一点就是"把目标设成 T 时实际会交付的组合"，与结果页同源（实测 T=11% 处两边
  差 1~2bp）。曲线形状：目标低于切点收益时是一条平线（切点组合），高于切点后沿前沿移动、夏普缓慢下降。
  代价：一次扫描约 1 分钟（30+ 个点 × 每点 1~2 轮迭代）。
- **优化目标（2026-10-13 定稿）：两阶段统一为 `max 夏普 s.t. 收益 ≥ 目标`**（stage-1 大类、stage-2 内部重排同一目标，
  实现见 `optimize_common.solve_max_sharpe`；约束绷紧时退回 `solve_min_var`，两者在该区间等价）。
  ⚠️ 因此**目标收益率只是下限，不是锚**：若它低于该约束下的切点收益，结果与滑杆无关——
  切点收益只由 μ 与协方差决定、**与目标无关**——目标低于切点收益时结果一动不动，
  目标高于切点才会沿前沿上移。界面必须写明这一点，并在结果页标出"实际 − 目标"的差额（`target_return`/`target_gap`）；
  关系可用 `Optimizer/tests/test_objective.py` 复算（那里用虚构池拆分，不含任何真实组合比例）。
  历史教训：曾经 stage-1 用 min 方差、stage-2 用 max 夏普，于是 stage-1 算出 10.00%、stage-2 按夏普把美股核心
  换成完全不同的标的组合、交付收益明显高于目标 —— 两个阶段目标不一致时，用户设的目标会被静默推翻。
- **目标函数的取舍**（`max 夏普` ≠ `max 收益`）：夏普是比值（对仓位规模不敏感），若 T 低于切点收益则**完全不起作用**（答案恒为切点组合）；
  若 T 高于切点收益，`max 夏普 s.t. ret≥T` 与 `min 方差 s.t. ret≥T` **给出同一个解**。
  `min 方差` 是凸问题（解唯一、与起点无关、只用协方差，对 μ 估计误差不敏感）；`max 夏普` 是比值目标（要多起点，
  且必须校验 bounds —— SLSQP 未收敛时返回的迭代点可能越界）。
- **先验审查（2026-10-13）已落地的清理**：① 写死值统一 0.5% 粒度（含溢价与兜底表）；
  ② 双债增强 4.5%→3.0%、QDII 稳健 4.5%→5.0%、美国高收益 6.5%→7.5%、黄金与商品 6%/5%→4.0%（按"无现金流"处理）；
  ③ 清除 HK_INTERNAL 六个内部标的及其先验（父大类 `O_HK_HSTECH` 早已不存在，整条链路恒为死代码）；
  ④ 短样本标的配长历史代理：香港高股息桶（真实标的历史仅 129 天）→ 恒指长历史、境内红利低波 100 桶（仅 532 天）→ 同类策略的长历史基金；
  `BUFFER` 无同质代理，保留短样本（σ 即其历史波动）；⑤ 日本宽基桶与其执行标的同源，不再「一套敞口两套参数」（此前桶用日经指数、执行用 ETF）。
- **2026-10-14 的第二轮清理**：① σ 人工下限整体取消（见上）；② **前瞻微调彻底退役** ——
  `forward_adjust.py` 删除，`params.py` 不再读 `forward_adjusted.json`，大类 μ 只来自校准文件 + 类别规则；
  ③ 大中华权益一级大类 `D_GREATER_CN` 过去没有行情、只能吃兜底值，现取**恒生指数长历史**作代表
  （`market_data.SOURCES`）；④ `RF` 默认值不再写死利率（改 `0.0` = 不假设无风险收益，正常路径由 App 注入）；
  ⑤ 结果页提示精简：去掉境内/境外资金池口径提示、基金在售/申赎状态的抓取计数、以及"某候选全部暂停申购"
  这类点名数据源的文案（后者的**结论**保留，只不再点名来源）。
- **标的代码会变，所以要定期核对命中**：美股内部池那一档标普500 宽基原写 `SPLG`（SPDR Portfolio
  S&P 500 ETF），发行方后来把代码改成 `SPYM`，Yahoo 对旧代码一律 404；而 Yahoo 搜 `SPLG` 命中的是
  LSE 上的 **Invesco 标普500低波 UCITS**（另一只基金，**不是**低波因子那一档，别照抄）。
  2026-10-14 用户定稿：这一档直接用 **`SPY`**（SPDR S&P 500 ETF Trust，数据最稳），
  key 与取数符号都是 `SPY`（`market_data.SOURCES`、`AssetCatalog`、US_INTERNAL、校准 JSON 全量改名）。
  ⚠️ 换代码时先确认**是不是同一只基金**（看成立日期与全名），再确认价格量级——SPY 与
  SPDR Portfolio 系列单位净值差着量级，拿错会让市值直接错几倍。
- **调试用得到的两个坑**：① 选源逻辑不能"谁点数多用谁"——窗口化后原油期货 `CL=F` 的点数会超过 `XOM`，
  把个股 σ 算成 114%；规则是"窗口内够长就用真实标的，代理只在真实标的铺不满窗口时顶上"（`_pick_series`）。
  ② `argparse` 的 help 走 %-格式化，中文说明里的 `%` 必须写成 `%%`。

- **优化器 Python 侧模块化重构进行中（ADR 0003，2026-10-17）**：目标是「纯计算内核 + 三种调用器 + services + models」。
  P0/P1 已落地，形态是**加法式**包 `Optimizer/portfolio_system/`（`models`/`core`/`callers`/`services`），
  **既有脚本一字未改、CLI 与 JSON 契约不变**。三条铁律且都有机械校验：
  ① `core/**` 与 `models/**` 禁网络/磁盘/子进程、禁模块级可变全局（`Optimizer/tests/test_core_purity.py` 静态守卫，守卫自身可证伪）；
  ② 三个调用器互不 import，只依赖 `core`/`models`/`services`；
  ③ `core` 只吃 `ProblemSpec` 里**已算好的数**（μ/σ/相关性/上下界/目标/rf），不许回头问 `params`/`market_data`。
  等价门 `Optimizer/tests/test_refactor_equivalence.py` 逐字段比对旧路径与新路径；**Python 侧用例只增不减（当前 33 文件 / 209 用例）**。
  **P4 已落地（2026-10-17）**：包已从 `Optimizer/scripts/` 上提到 `Optimizer/portfolio_system/`（`git mv` 保历史）。
  路径策略：入口三脚本 + `optimize_common`/`params` 两个 shim 自带「包根 = scripts 的父目录」bootstrap；8 个测试的包根由 `PM_OPT_SCRIPTS` **派生**（旧布局自动回退，祖先对照仍成立）；
  `build_app.sh` 增拷包目录 + 递归清 `__pycache__`，**未改任何 Swift**（bundle 与仓库保持 `Optimizer/{scripts,portfolio_system,data,.venv}` 同构，`scriptURL` 解析链不变）。
  **P2 已落地（2026-10-17）**：`optimize_common.py` 的纯函数与 `params.higham_psd` 已搬进 `core/`（`linalg`/`solver`/`constraints`），
  容差常量搬进 `core/constants.py` 并由旧位置**再导出**（旧名继续可用、无第二处字面量）；`core`/`callers` 已无模块级全局读取。
  **P3-a 已落地（2026-10-17）**：core 的两处延迟转发已消 —— `build_broad_cov` 搬进 `core/linalg`；active 上限表改**显式入参**
  （`active_cap_map(assets, internal_pool_keys=())`，内部池清单由调用方传，覆盖 US/JP/GC 内部标的），`apply_active_caps` 改为
  **返回触顶记录、零模块级全局写**；告警载体统一为 `core/warnings.py` 的 `CodedWarning(str)`（`optimize_portfolio._Warn` 变别名，文本与 code 逐字不变）。
  ⚠️ **P3-a 的教训（`test_core_purity.py` 的盲区）**：静态 AST 守卫**看不见函数体内的调用期延迟 import** ——
  `constraints` 曾用它把 `optimize_portfolio`/`fund_pipeline` 拉进 `sys.modules`。已新增 `Optimizer/tests/test_runtime_deps_guard.py`
  （子进程内**真的调用** core 入口后断言 `sys.modules` 干净，且**可证伪**）。**两层守卫缺一不可**，都写进了 ADR §3。
  **P3-b1 已落地（2026-10-17）**：袖内/阶段二纯数学搬到 `core/stage2.py`（8 个）+ `core/sleeve_params.py`（5 个）+ `core/constraints.py`（`max_return_given_mu`），
  依赖全部改**显式入参**（内部池表/相关性/target/rf/pool_specs）、触顶经 `cap_hits_out` 回传；旧位置留 8 个薄壳 + 6 个同名再导出（既有 import 不破）。
  **2026-10-18 清理**：其中 8 个全仓零引用的薄壳（`_reseed_sleeves` / `_sleeve_failure_reason` / `_undeliverable_warning` /
  `_sleeve_tangency` / `_required_sleeve_mu` / `_floor_next_params` / `_sleeve_tangency_all` / `_sleeve_levels_for_feasibility`）已删除；
  仍被生产/测试使用的再导出保留，接口不变。
  **2026-10-18 二次清理（死代码尾巴）**：删除上次薄壳删除后遗留的再导出/导入（`optimize_portfolio.py`）、
  `asset_classes.AGGREGATES`、两个零引用薄包装与 `_INTERNAL_POOL_KEYS`、`core/optimizer.py` 未读的 `rf_default`
  构造参数，以及 4 个测试的未使用导入与 1 个死测试助手；`core/fixpoint.py` 的两份逐字相同的 `ceiling` 闭包
  抽成 `_ceiling_solver`。`requirements.txt` 补 `pandas`（`market_data.py` 模块级使用；运行时经 `held_assets` 加载，
  按 requirements 重建 venv 时缺它会在启动即失败）。**有意保留**：`services/` ABC、`core/errors.SingularCovariance`、
  `core/optimizer.py` + `core/sleeves.py` 的 DTO 路径（等价门/ADR 预留）、测试文件各自自带的 runner（祖先树单文件
  对照要求自包含，不做去重）。用例数不变（33 文件 / 209；Swift 87）。
  验证：`VERIFY-P3b1: OK`（逐行等价、非假搬迁、core 无上层 import、守卫覆盖新入口且两种注入都能红）；用例 115→118（新增 symtable「未定义全局名」闸 2 项 + 守卫证伪 1 项）。
  ⚠️ **兼容性注记（核验实测）**：薄壳**不等于**完整 monkeypatch 兼容 —— 模块级**变量**（`TARGET_RETURN`/`RF`/`CAP_HITS`/各 INTERNAL 表）仍在调用时解析（兼容），
  但模块级**函数**交叉打桩（例如替换 `OP.stage2_covariance`）**不再生效**；仓内无任何测试/生产代码这样打桩，产品路径无影响。
  **P3-b2 已落地（2026-10-17）**：`core/fixpoint.py`（14 个袖内可行性/下限/择优函数）+ `core/engine.py`（`_solve_stage1`/`_solve_once`/`solve_portfolio`）+ `SleeveUniverse`（外部数据表显式入参）+ `SleeveHooks`（5 个策略函数注入）。
  **`core/sleeves.py` 的调用期委托已删 —— core 里再无任何上层依赖**（`VERIFY-P3b2: OK`：干净子进程真调用 `solve_sleeves` 后 sys.modules 新增禁用模块为空）。
  设计要点：内部池表经 `ProblemSpec.extract["sleeve_universe"]` 显式传入，**缺了抛 `InputError`**（不静默跑空）；薄壳每次调用现取模块级名字构造 hooks，于是**变量与函数两类打桩都继续生效**。用例 118→119。
  ⚠️ **P3-b3（部分落地，2026-10-17）**：① `services/` 按真实函数名接线；④ **P3 正式门已达成** —— `sensitivity_analysis.py` 的模块级 import 已删，改经 `callers/sensitivity_runner.SensitivityRunner.solve_point` 直连 core（新增 `test_sensitivity_offline.py` 4 项，修复前 2/4 双红）。依赖搬移而非复制（SP500 表 → `core/stage2.py`；袖种子 → `core/sleeve_params.SLEEVE_SEEDS` + `build_pool_specs()`）。`VERIFY-P3b3: OK`。
  ✅ **③ 已落地**：`NewAssetRunner.inject_test_assets` 接管注入（保留 exit 3 契约）；**常量收口已完成**（`core/constants.py` 增 14 个语义常量，替换 core 41 处 + 入口脚本 7 处，值不变）；`PresetRunner` 已接管 `build_result_summary`/`_build_benchmark`（入口 1187 → 910 行；再经下面 ② 的后半收敛到 736 行）。
  ✅ **② 的**后半已落地（2026-10-17）**：`main()` 从约 315 行收敛到 80 行（文件 917 → 736 行），只剩「argparse → RunRequest → `PresetRunner.run` → 写 JSON」
  ＋装配层职责（`RF`/`TARGET_RETURN` 全局注入、4 类错误→退出码映射、落盘）；编排（联网管线 → 资产装配 → 新标的注入 → 求解 → 结果汇总）搬进
  `callers/preset_runner.py`（`PresetRunner._run_pipeline` + `PresetDeps`，上层依赖全部**显式注入**）、`build_output_payload`（结果页 JSON 唯一装配点）、
  `select_domestic_funds`（选基规则）；无 `deps` 时 `PresetRunner.run` 仍走原求解路径（等价门用的那条，未动）。
  验收：`tmp/slim/equivalence.py` 逐字节等价门 5/5（high/low/inject/nodom/notest，含退出码、stdout、summary；canned 管线桩），
  核验席位造红 4 类注入全红；真实联网 CLI 的决策载荷与改造前**逐字段相同**；新增跟踪守卫 `test_main_slim.py`（4 项，旧 `main()` 上 1/4）
  + `test_preset_pipeline_offline.py`（5 项，受控桩；祖先树 ImportError = 红）。`VERIFY-MAINSLIM: OK`（证据 `tmp/agent-team/verify-mainslim/`）。
  ⚠️ 仍留（有意）：`data_fetcher` 运行时无生产调用点（行情在校准阶段取）；`models/POOL_SUM_EPS` 有意留在 models；入口的 `_inject_test_assets` 薄壳
  仅为既有测试/兼容保留（生产走 callers）；`OP.select_domestic_funds` 的 monkeypatch 兼容性已失效（仓内无调用方）。

- **「加新标的 → 夏普反而下降」修复（2026-10-17，两个独立根因）**：
  ① **东财时间戳时区错位**：`eastmoney_nav` 曾按 UTC 取日（东财 `x` 实为北京时间 00:00）→ 境内基金日期整体早一天，
  与 Yahoo 序列配对时相关性被系统性带偏（同敞口实测 −0.06 vs 修正后 +0.97；校准里基金×Yahoo 288/946 对全受影响）。
  已改 `market_data._eastmoney_date`（UTC+8）并**重新生成 `Optimizer/data/calibrated_params.json`**（corr 833 对变化；
  非基金×Yahoo 漂移 ≤0.0041，证明变化主要来自日期修正）。旧 bundle 备份在 `tmp/agent-team/evidence/diag-sharpe/`。
  回归：`test_market_data_dates.py`（5 项，旧码全红）。
  ② **袖内固定点多解**：目标播种分叉会锁进较差固定点，而更高初值存在更好且同样自洽的固定点（模型-交付差 ≤0.5pp）。
  `core/engine.solve_portfolio` 现拆为 `_solve_fixpoint_once` + 包装：**仅当** history 出现 `sleeve_seeded_levels` 时，
  用确定性种子（切点 μ 与无上限最大收益 μ 的线性插值 λ=0.35/0.50）重启并择优（交付发生 → 缺口 → 模型差合格线 → 交付夏普 → 波动），
  输出加法式 `sleeve_restarts` 审计；未触发只跑一遍、逐字节不变。被弃候选的 warnings/cap_hits 按候选隔离。
  回归：`test_sleeve_restart.py`（9 项，祖先必红）；旧 bundle 捕获模型上注入场景交付夏普由明显劣于主运行回到优于主运行（数值只在 tmp）。
  ③ **P1 可观测性/硬化**：`build_broad_cov(missing_out=…)` + 结果 JSON 加法式 `corr_missing`；PSD 修复统计 `cov_psd`（eigmin 前后/改写对数/最大 Δ）；
  `bounds_arrays` 让 `AssetSpec.lower/upper` 生效；`active_cap_map(cap=…)` + `GoalSpec.active_cap` 生效（默认值不变）；`max_feasible_return` 校验 bounds/守恒。
  新测试：`test_cov_diagnostics.py`（4 项）+ `test_solver_hardening.py`（4 项）。
  ✅ **Swift 侧同款时区 bug 已修（2026-10-18）**：`EastmoneySource` 增加 `cnTimeZone = "Asia/Shanghai"`，
  `parseNav`（单位净值 / 累计净值两条路径）按北京时间取日 → App 里基金 K 线/最新价的日期标签与 Python 侧口径一致；
  回归：`Tests/PortfolioCoreTests/EastmoneySourceTests.swift`（4 项，修复前 3/4 红）。
  ✅ **`prices_cache` 残留已自愈（2026-10-18）**：缓存目录带取数口径版本标记（`PRICES_CACHE_SCHEMA`），
  标记缺失/不一致/损坏时整体清空重建 —— 崩溃 / 强制退出 / 跨版本升级都不会再把旧日期喂回调用方；
  回归：`test_prices_cache.py`（6 项，祖先树 0/6）。

- **持仓纳入候选（2026-10-18，修「实盘持有但不在候选目录的标的永远选不出」）**：主优化候选池过去只有静态目录
  （`US_INTERNAL`/`BROAD_ASSETS`/境内开放基金池）；`us_equity.holdings` 只收 `asset_class=="us_equity"` 的持仓，
  且 `us_core_params` / stage-2 都只认目录内 key → 目录外持仓被**三重静默丢弃**，只有「新标的测试」能把它注入。
  现在 App 在 extract 写 `held_assets`（全部 value>0 持仓）+ `include_unmodeled_holdings`，装配层
  （`Optimizer/scripts/held_assets.py`，主流程与敏感性共用）把「未建模」持仓按**新标的测试同一套估计器**注入为
  顶层 what-if 腿（`H_` 前缀、`test_class=active` → 10% 上限；目录外持仓不进袖内模型、不计入 O_US_CORE 袖）。
  modeled 判定 = 内部池 key ∪ `market_data.SOURCES` 执行符号 ∪ 本次开放池选中代表基金 6 位代码 ∪ 当前顶层资产 key/fund_code。
  加法式输出：detail `held{included,skipped,counts}`、summary `held_assets` + `assets[].is_held`（注入行必须进清单，
  否则 `summary.missing_weights` 自检会误报）、告警 code `held.skipped` / `held.us_equity_outside_catalog`。
  单只数据不可用只 skipped + 告警、不中断；无 `held_assets` 字段时输出逐字节不变。回归：`test_held_assets.py`（12 项）。
- **敏感性锚定主结果 + 显式多初值（2026-10-18，修「曲线夏普低于主结果」）**：扫描逐点用上一点袖参数热启动，
  会收敛到另一个**自洽但更差**的固定点（1–2 轮即判 converged）；旧 `sleeve_restarts` 只在「目标播种分叉」触发，
  扫描路径从不触发、也不与主结果对照。现在 summary 在 `emit_sensitivity_anchor` 为真时输出 `sleeve_params`
  （App 总开；CLI/旧提取文件不多键），`sensitivity_analysis.py` 增 `--anchor-target/--anchor-sleeve/--anchor-ret/--anchor-vol`：
  锚点目标并入网格且该点**直接用主结果袖参数求解**（与结果页同一固定点，`anchor.matched` 逐位比较、容差 1e-6）；
  其余点（**仅锚定模式**；无锚点路径逐字节不变）：首个点直接 canonical 起跑（有主结果袖参数 → anchor，否则 λ=0.20 种子）；
  约束松弛点（target ≤ 切点估计）**直接复用上一 slack 点的交付结果、不求解**（同一组切点组合、不同目标下限）；
  绷紧点（切点 < target ≤ 锚点目标）用主结果袖参数单次求解（失败才一次独立 prev 重试）；
  高于锚点目标的点用主结果袖参数单次求解、fallback/未收敛即判停（不重试）。
  任何重试都是独立单次求解，不再出现“canonical 当 alt → 引擎把 base 重跑一遍”的重复求解。
  点级加法键 `fallback` 与 `chosen_start`；顺序扫描**遇到首个回退点即停**（不再补高价角点、不再补 max-feasible 点）；
  `--step-max` 真正约束补点。实测（真实数据 34 个目标点）：旧实现 838.97s → 中间版 702.33s → 本版 **156.18s（≈2.6 分钟，目标 ≤180s）**；29 次完整求解 / 34 点 = 0.853 次/点（松弛点复用不求解，其余每点 1 次）。
  **gating**：`sleeve_history[*].zero_weight_sleeves` 只在外部热启动/alt 路径写、`sleeve_restarts.reason` 只在 alt 路径写
  → 主运行与 canned 等价门严格 5/5 不变。回归：`test_engine_alt_starts.py`（11 项）、`test_sensitivity_anchor.py`（19 项）；
  `test_warning_codes.py` 4/4（`sensitivity.anchor_mismatch` 从 `callers/sensitivity_runner.py` 用字面量 code 发出）。
- **已知残留（2026-10-18）**：主运行仍是单路径冷启动（若冷启动本身落在较差固定点，本轮不额外探索，留待全局多初值 A/B）；
  零权重袖的假设参数仍不参与验证，只在外加热启动/alt 路径的审计键里可见。
  ⚠️ 修正日期后校准矩阵仍非 PSD（内部池 eigmin ≈ −0.20）：收缩/稳健估计（Ledoit-Wolf 等）属 P2，默认关，需 walk-forward A/B 后再定。

## 13. Swift Charts 踩坑速查（财务分析统计图，2026-09-08 实战记录）
- **同一图里多组 LineMark 若不加 `series:`，会被按数据顺序连成一条线**——上界/下界、正负段分开给点时，末端点会直插首点画出一条斜跨全图的长弦（v0.2 堆积图"蓝色斜线"bug）。每组线单独标 `series: .value(...)`，或干脆别画。
- **X 轴是 String 类别值**：类别数量跟随数据，不会因为视觉上不连续就自动排序友好——**先保证传入数组已按时间升序**（`2023 Q4` 混到末尾就是乱序数据没排）。
- **`ChartProxy.value(atX:)` 返回可选值且依赖 plotFrame 坐标换算**：悬停要用 `plotFrame` 原点减偏移（见 FinancialChartPanel 的 ChartHoverModifier）；老写法 `proxy.value(at:as:)` 要传 `(X, Y).Type` 元组，别只传单边类型。
- **@ViewBuilder 里不能有 `rows.append` 这类语句副作用**——会导致 `'()' cannot conform to View`；要组数据就提前在 builder 外算好数组。
- **游离辅助函数返回 `some View` 的没法用在 ChartContentBuilder 里**（`'some ChartContent' conform to 'View'` 报错）；AreaMark/BarMark 直接内联在 ForEach 里。
- **ForEach 数据必须 Identifiable 或给 `id:`**；`ForEach([Int])`、`ForEach(Int)` 会炸，用 `Array(enumerated()), id: \.offset`。
- **"compiler is unable to type-check this expression in reasonable time"** = 单个 Chart builder 表达式太重。解法：把数组在 builder 外预先算成简单 struct 数组，builder 内只剩 ForEach + Mark。
- **macOS 没有内置悬浮 tooltip**，要 `chartOverlay + GeometryReader + onContinuousHover` 手写。
- **堆积图负值**：有负层时正负分开累计（正值堆上、负值沉底），否则分界线会画歪。
  **唯一例外是财务分析「本金与市值」图**：那四个正层（累计股息 → 累计利息 → 累计已实现资本利得 → 本金）相加 ≡ 总成本，
  所以最上面那条「未实现资本利得」带**不沉底**，而是两端锚在总成本线上——为正时叠在总成本之上（色块上界 = 总市值），
  为负时压在总成本之内（本金层顶部那一截被它盖住，色块上界 = 总成本）。这是有意为之，别再"修"回沉底。
- **跨零翻转的带子别用 `catmullRom`**：它在符号翻转处过冲，实测会把负值带插到 0 轴以下、整条带横穿全图（2026-10-09 实测）。
  不过冲的插值只有 `.linear`（点间走直线）与 `.monotone`（圆滑但不过冲）；**图 5 现用 `.linear`**（想更圆滑换 `.monotone` 即可，两边都是一词之改，别用 catmullRom）。
- **图 5 的实现口径**：X 轴是**数值轴**（季度序号），不是类别轴 —— 因为交点落在两个季度之间，类别轴放不下：
  四个正层照旧走 `foregroundStyle(by:)` 的堆积（相加 = 总成本，形态与改动前一致）；「未实现资本利得」带**不进堆叠**，
  另画一条独立序列 `AreaMark(x:yStart:yEnd:)`（x 用季度序号），并在**两条线相交处插一个采样点**（`t = d0/(d0−d1)`）
  —— 于是带子在那里厚度真正归零、再翻到另一侧。轴标签只在整数刻度出（`AxisValueLabel` 里反查季度名）。
  悬停：数值轴下 `proxy.value(atX:)` 返回 `Double`，`ChartHoverModifier` 加了 `numeric` 开关折算到最近季度（**其余四张图仍走类别轴**）。
  插值统一 `.linear`（点间走直线，不过冲）。
  **本金参考线**：值 =「本金」字段本身（离横轴的距离 = 本金金额；不是本金层的下边缘，也不是累计已实现回报），
  常显、不进图例、不进堆叠；样式 = **亮绿**粗虚线 `Color(hue: 0.30, saturation: 0.82, brightness: 0.95)` +
  `lineWidth 2.5, dash [5, 3]`（原先的墨绿在砖红/金层上发糊、在深色背景里更糊）。这条线永远落在有填充的带子内，不会落到空白背景上。
- **`AreaMark` 的 `yStart/yEnd` 会「按点归一化」**：每个数据点先取小者为下界、大者为上界，再各自插值 ——
  所以在**类别轴**上"两条边界线在两个季度之间相交"这个形状画不出来（带子只会整体滑过成本线；
  实测把 `min/max` 换成直接给 `总市值/总成本` 两条线，两种写法**逐像素完全相同**，2026-10-09）。
  要真交点就得换数值轴 + 在交点补采样点（图 5 就是这么做的）。注意 `cmp` 比字节会报"不同"，那是 PNG 压缩差异，要比像素。
  踩坑记录：把五层**全部**改成显式 `yStart/yEnd` 的 `AreaMark` 会被合并成一条（只剩一层）；`BarMark(x:yStart:yEnd:width:)` 传裸 `Double`
  会静默落到另一个 init 画成全高柱；`RectangleMark` 的 `yStart` 是 `CGFloat`。**混合写法**（4 层照旧堆积 + 1 条显式带）才可行。
- 设计教训：**一图一信息**。Y 轴截断（如 20%~180%）会把小波动放大成"过山车"；混合柱+双轴线在一个图里基本看不懂。用截断轴前先想清楚要不要 `chartYScale(domain: 0...)`。

## 14. 工作流习惯
- 中文 commit message，前缀：`feat(scope):` / `fix(scope):` / `chore:`。
- 改版前先跑全量测试；发布流程见 §5；**提交/发布前跑 `scripts/privacy_check.sh`**（装了钩子会自动跑）。
- **版本同步纪律（2026-10-16 定稿）**：每次 bump 版本，必须同步更新本文件 §0 的「当前版本」与「pre-release / tag 计数」，以及 `tmp/agent-team/HANDOVER.md` 的基线锚点；**改动测试用例数时同步 §4/§7 的用例数与 HANDOVER §6 的测试清单** —— 现状速览过期会直接误导后续所有成员。
- `.gitignore` 已忽略：`.build/` `Optimizer/.venv/` `tmp/` `dist/` `__pycache__/` `*.pyc` `.DS_Store`。
  不要把 venv、构建产物、含个人数据的文件加进来；个人数据目录与表格文件就在工作区里，但**不是本仓库内容**，勿提交。
