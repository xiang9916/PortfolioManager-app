# AGENTS.md — PortfolioManager 开发须知

> 给在此仓库工作的 AI / 开发者。**2026-10-02 汇总**（上一版 2026-09-08）。
> ⚠️ **本文件在仓库内、会随仓库一起公开** —— 所以这里只写可公开的事实：不含真实持仓（代码/数量/成本/金额）、
> 不含组合规模与市场构成、不含个人表格名与构建机绝对路径。内部细节请留在仓库外，不要写进本文件。动代码/发布前先读一遍。

## 0. 现状速览
- 原生 macOS (SwiftUI) 个人投资组合 App，替代「.numbers 手工维护 + 终端跑 Python 优化器」。
- 当前版本 **v0.4-beta3**（版本号只存在于 `scripts/Info.plist`）；Schema **v8**；GPL-3.0；作者 xiang9916。
- 线上仓库：`xiang9916/PortfolioManager-app`，13 个 pre-release + 14 个 tag 齐全。
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
- 该命令只取长度 ≥5 的 key，3–4 字符的短代码查不出来；这类短代码多为通用代号，
  仍需人工确认仓库里没有把它们与持仓/数量/金额写在一起。
- release notes 也要单独扫（它在 GitHub 上，不在仓库里）：`gh release view <tag> --json body --jq .body` 后同样 grep。
- **示例数值也必须虚构。** 最容易漏的一处是「拿真实数字当例子说明格式化 bug」—— 真实历史里就这么漏过一次
  （release notes 里写了两个真实金额作为 `%g` 舍入的对照）。扫 notes 时那条类别 grep 一旦命中千分位写法，就得改。
- 测试一律用**虚构 fixture**；季度指标的期望值不要手抄，用 `QuarterlyMetrics.compute` 跑出来再固化。

### 历史教训（为什么上面这些规则这么硬）
- 本项目真出过一次隐私泄露，**泄露渠道全部是「人写的文本」**：release notes、测试 fixture、代码注释、UI 文案。
  DMG 里从来没有数据库/金额 —— 所以「打包产物干净」不等于「没泄露」。
- 处置动作：脱敏并重发 notes、fixture 全部改虚构、注释/UI/CLI 改中性示例、脚本里的构建机路径改环境变量、
  重打发布包，并**改写全部历史 + 重建仓库**（因此旧 SHA 现已取不回，见 §2）。
- 一句话：这类事故的代价是整个仓库重做，所以发布前那两条 grep 必须跑。

## 2. 仓库状态与历史（会影响你看到的 SHA）
- remote `origin https://github.com/xiang9916/PortfolioManager-app.git`，分支 `main`。
- **重建于 2026-09-30**：所有提交 SHA 都变了，更早文档/对话里的 SHA 不再对应，`git fetch origin <旧SHA>` 会失败（这是想要的效果）。
- 线上 13 个 release（v0.1-beta2 … v0.4-beta2）已逐一还原：标题、pre-release 标记、脱敏 notes、DMG —— 按 GitHub asset `digest` 逐个核对与本地归档**字节一致**。
- 本地分支 `backup/beta3-beta4-work`（0.3-beta3/4 时期的备份线，比 main 多 4 个提交）**从未推送过**；要继续用就 `git push -u origin backup/beta3-beta4-work`。
- 无 CI workflow：DMG 本地构建 + 手动 `gh release create` 上传。

## 3. 恢复套件（在仓库内 `tmp/`，已被 .gitignore 忽略）
- `tmp/pm-repo-20260930.bundle` —— 全 refs 备份（main + 14 tags），实测 `git clone <bundle>` 可还原。
- `tmp/release-archive/` —— 13 份脱敏 notes、13 份 release 元数据、`SHA256SUMS`、`tags.txt`、`repo-settings.json`，
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
- 外部数据目录：`DSH_FINANCE_DIR`（默认 `~/Finance/tmp`）；aistockresearcher 脚本目录：`AISTOCKRESEARCHER_DIR`。
  **不要写死 `/Users/<某人>/…` 路径**（构建机绝对路径是隐私泄露源，见 §1）。
- venv 解释器 `/opt/miniconda3/bin/python3`（实测 3.14.x）。
- futu-api 仅声明于 requirements，当前无代码 import；行情走 Yahoo/Eastmoney 公开源。仓库不得出现任何 API key/凭据。
- 「新标的测试」= 临时加 ticker 重跑，不改持仓，结果在弹窗（testOptimization 状态）。

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
- 设计教训：**一图一信息**。Y 轴截断（如 20%~180%）会把小波动放大成"过山车"；混合柱+双轴线在一个图里基本看不懂。用截断轴前先想清楚要不要 `chartYScale(domain: 0...)`。

## 14. 工作流习惯
- 中文 commit message，前缀：`feat(scope):` / `fix(scope):` / `chore:`。
- 改版前先跑全量测试；发布流程见 §5；**发布前跑一遍 §1 的隐私自查**。
- `.gitignore` 已忽略：`.build/` `Optimizer/.venv/` `tmp/` `dist/` `__pycache__/` `*.pyc` `.DS_Store`。
  不要把 venv、构建产物、含个人数据的文件加进来；个人数据目录与表格文件就在工作区里，但**不是本仓库内容**，勿提交。
