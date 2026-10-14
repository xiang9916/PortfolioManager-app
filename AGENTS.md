# AGENTS.md — PortfolioManager 开发须知

> 给在此仓库工作的 AI / 开发者。**2026-10-02 汇总**（上一版 2026-09-08）。
> ⚠️ **本文件在仓库内、会随仓库一起公开** —— 所以这里只写可公开的事实：不含真实持仓（代码/数量/成本/金额）、
> 不含组合规模与市场构成、不含个人表格名与构建机绝对路径。内部细节请留在仓库外，不要写进本文件。动代码/发布前先读一遍。

## 0. 现状速览
- 原生 macOS (SwiftUI) 个人投资组合 App，替代「手工表格维护 + 终端跑 Python 优化器」。
- 当前版本 **v0.5-alpha.2**（版本号只存在于 `scripts/Info.plist`）；Schema **v8**；GPL-3.0；作者 xiang9916。
- 线上仓库：`xiang9916/PortfolioManager-app`，16 个 pre-release + 17 个 tag 齐全。
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
swift test  --disable-sandbox          # 50 个用例；联网用例需 PM_LIVE_TESTS=1
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
- **发布产物固定两件套**：`dist/PortfolioManager-<版本>.dmg`（内含 app + `/Applications` 快捷方式，拖拽安装）
  与 `dist/PortfolioManager-<版本>.zip`（**顶层只有一个 `PortfolioManager.app`**，用 `ditto` 打包以保留符号链接、
  可执行位与扩展属性）。`--dmg` 隐含 `--zip`；只想出 zip 就传 `--zip`。
- **DMG 里不再放「一键安装.command」**（自 0.4-beta3 起）：它自身同样带隔离标记、双击第一次照样被 Gatekeeper
  拦住，收益为零却要多维护一个脚本。仓库里的 `scripts/install_dmg.sh` 保留，供有仓库的人从终端安装。
- **内置隐私加固（勿删）**：① venv 拷完后清 `__pycache__` 并把 `/Users/<user>/` 重写成 `/build`；
  ② 二进制 debug info 里的 `/Users/<user>` 在 codesign **之前**做等长替换成 `/tmp/pmbuild`。
  副作用：DMG 体积 85MB → 48MB（少了 .pyc），首次跑优化器会重新编译缓存。
- 版本 bump：只改 `scripts/Info.plist`（`CFBundleShortVersionString` + `CFBundleVersion` 同步）→
  commit `chore: bump version to X.Y` → annotated tag `vX.Y`（message `PortfolioManager X.Y`）
  → `gh release create --prerelease`，notes 中文 + **两个附件各自的 SHA256 行** + 同时挂 DMG 与 zip 两个 asset。
- 上传后必须核对：`gh api repos/xiang9916/PortfolioManager-app/releases/tags/<tag> --jq '.assets[] | .name, .digest'`
  与本地 `shasum -a 256` 一致，且 notes 里的 SHA 行同步更新。
- zip 上传前必须**解压回验**：`ditto -x -k <zip> <tmpdir>` → `codesign --verify <tmpdir>/PortfolioManager.app`
  必须通过（zip 若丢了符号链接或可执行位，封条会碎）。
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
- 股息税率锚定同一个市场结论（`DividendTax.rate(market:)`），JP/SG 未配置 → nil。

## 7. 测试
- `swift test --disable-sandbox` → **50 个用例全绿**（其中 2 个联网用例按设计跳过，需 `PM_LIVE_TESTS=1`）。
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
- **安装方式**：打开 DMG 把 app 拖进「应用程序」（或解压 zip 后拖过去），首次打开在
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
- futu-api 仅声明于 requirements，当前无代码 import；行情走 Yahoo/Eastmoney 公开源。仓库不得出现任何 API key/凭据。
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
- **标的代码会变**：`SPLG`（SPDR Portfolio S&P 500 ETF）的发行方已把代码改成 **SPYM**，Yahoo 对旧代码
  一律 404；而 Yahoo 搜 "SPLG" 命中的是 LSE 上的 **Invesco 标普500低波 UCITS**（另一只基金，别照抄）。
  仓库里保留 `SPLG` 作为**内部 key**（数据库/相关性/校准 JSON 的主键，改 key 要迁移），
  但取数符号（`market_data.SOURCES` 与 `AssetCatalog`）指向 `SPYM`。
- **调试用得到的两个坑**：① 选源逻辑不能"谁点数多用谁"——窗口化后原油期货 `CL=F` 的点数会超过 `XOM`，
  把个股 σ 算成 114%；规则是"窗口内够长就用真实标的，代理只在真实标的铺不满窗口时顶上"（`_pick_series`）。
  ② `argparse` 的 help 走 %-格式化，中文说明里的 `%` 必须写成 `%%`。

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
- `.gitignore` 已忽略：`.build/` `Optimizer/.venv/` `tmp/` `dist/` `__pycache__/` `*.pyc` `.DS_Store`。
  不要把 venv、构建产物、含个人数据的文件加进来；个人数据目录与表格文件就在工作区里，但**不是本仓库内容**，勿提交。
