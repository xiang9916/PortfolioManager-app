#!/usr/bin/env bash
# 隐私自查（AGENTS.md §1 的自动化版本）。
#
# 用法：
#   scripts/privacy_check.sh                  # 扫整个工作区（发布前必跑）
#   scripts/privacy_check.sh --staged         # 只扫暂存区（pre-commit 钩子）
#   scripts/privacy_check.sh --message <file> # 扫提交信息（commit-msg 钩子）
#   scripts/privacy_check.sh --install        # 把钩子装进 .git/hooks
#
# 检查项：
#   ① 真实持仓 key（库里 length>=5 的标的代码）不得出现在仓库文本里
#      —— 例外是"候选池"文件（AGENTS.md §1 明确放行的那几个）
#   ② `/Users/<用户名>/`（构建机绝对路径）与 `123,456.78` 这类千分位金额：必须零命中
#   ③ 本地黑名单 tmp/privacy-numbers.txt（一行一个模式，gitignore 内）：可选，
#      用来挡"本地跑出来的组合级数字"（优化权重、组合收益/波动/夏普等）
#
# 退出码非 0 表示发现疑似泄露，提交/发布应当中止。
set -uo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"

EXCLUDES=(--exclude-dir=.git --exclude-dir=.build --exclude-dir=dist --exclude-dir=tmp
          --exclude-dir=.venv --exclude-dir=__pycache__ --exclude-dir=.swiftpm-cache)

# 分级：① 候选池文件（装的是候选资产全集，本来就在仓库里公开，不是持仓清单）
#       ② AGENTS.md §1 已登记的"通用公开行情代码"命中（需人工确认，不算失败）
POOL_RE='(^|/)(AssetCatalog\.swift|params\.py|market_data\.py|calibrated_params\.json|asset_classes\.py|bond_prior\.py)$|^\./Optimizer/tests/'
GENERIC_RE='AssetMarketTests\.swift$|AssetEditorViews\.swift$|YahooFinanceSource\.swift$|^\./AGENTS\.md$|^\./README\.md$' 

# 自身含规则文本，不参与 ②（同 AGENTS.md §1 的既有例外）
SELF_EXCLUDE="--exclude=privacy_check.sh --exclude=AGENTS.md --exclude=build_app.sh"

MODE="worktree"
MSG_FILE=""
case "${1:-}" in
  --staged)  MODE="staged" ;;
  --message) MODE="message"; MSG_FILE="${2:?--message 需要文件路径}" ;;
  --install)
    mkdir -p .git/hooks
    cat > .git/hooks/pre-commit <<'HOOK'
#!/usr/bin/env bash
# 由 scripts/privacy_check.sh --install 生成
exec "$(git rev-parse --show-toplevel)/scripts/privacy_check.sh" --staged
HOOK
    cat > .git/hooks/commit-msg <<'HOOK'
#!/usr/bin/env bash
# 由 scripts/privacy_check.sh --install 生成
exec "$(git rev-parse --show-toplevel)/scripts/privacy_check.sh" --message "$1"
HOOK
    chmod +x .git/hooks/pre-commit .git/hooks/commit-msg
    echo "已安装 .git/hooks/pre-commit 与 commit-msg"
    exit 0 ;;
  "") ;;
  *) echo "未知参数：$1"; exit 2 ;;
esac

FAIL=0
report() { printf '  ✗ %s\n' "$1"; FAIL=1; }

echo "== 隐私自查（模式：${MODE}）"

# ---------- ① 真实持仓 key ----------
if [ "$MODE" != "message" ]; then
  DB="$HOME/Library/Application Support/PortfolioManager/portfolio.db"
  if command -v sqlite3 >/dev/null 2>&1 && [ -f "$DB" ]; then
    TOKENS="$(mktemp)"; trap 'rm -f "$TOKENS"' EXIT
    # 只取长度 ≥5：4 位以内的短代码会在小数里假命中（0.1256 里含 "1256"）
    sqlite3 "$DB" "select distinct key from assets where length(key)>=5" > "$TOKENS" 2>/dev/null || true
    if [ -s "$TOKENS" ]; then
      if [ "$MODE" = "staged" ]; then
        HITS="$(git diff --cached -U0 | grep '^+' | grep -v '^+++' | grep -oFf "$TOKENS" | sort -u || true)"
        [ -n "$HITS" ] && report "暂存区里出现真实持仓 key："$'\n'"$(echo "$HITS" | sed 's/^/      /')"
      else
        FILES="$(grep -rIlFf "$TOKENS" . "${EXCLUDES[@]}" 2>/dev/null || true)"
        POOL="$(echo "$FILES" | grep -E "$POOL_RE" || true)"
        GENERIC="$(echo "$FILES" | grep -E "$GENERIC_RE" | grep -Ev "$POOL_RE" || true)"
        OTHER="$(echo "$FILES" | grep -Ev "$POOL_RE" | grep -Ev "$GENERIC_RE" || true)"
        [ -n "$POOL" ] && echo "  · 候选池文件命中（预期，跳过）：$(echo "$POOL" | wc -l | tr -d ' ') 个"
        [ -n "$GENERIC" ] && echo "  · 需人工确认（AGENTS §1 已登记的通用公开代码）："$'\n'"$(echo "$GENERIC" | sed 's/^/      /')"
        [ -n "$OTHER" ] && report "白名单之外出现真实持仓 key（按泄露处理）："$'\n'"$(echo "$OTHER" | sed 's/^/      /')"
      fi
    else
      echo "  · 库里没有 length>=5 的 key，跳过 ①"
    fi
  else
    echo "  · 找不到库或 sqlite3，跳过 ①"
  fi
fi

# ---------- ② 路径 / 千分位金额 ----------
PATTERN='/Users/[a-zA-Z0-9_.-]+/|[0-9]{2,3},[0-9]{3}'
if [ "$MODE" = "message" ]; then
  HITS="$(grep -InE "$PATTERN" "$MSG_FILE" || true)"
elif [ "$MODE" = "staged" ]; then
  HITS="$(git diff --cached -U0 | grep '^+' | grep -v '^+++' | grep -InE "$PATTERN" || true)"
else
  HITS="$(grep -rInE "$PATTERN" . "${EXCLUDES[@]}" $SELF_EXCLUDE 2>/dev/null || true)"
fi
[ -n "$HITS" ] && report "绝对路径/千分位金额命中："$'\n'"$(echo "$HITS" | sed 's/^/      /' | head -20)"

# ---------- ③ 本地黑名单（组合级数字） ----------
BLACK="tmp/privacy-numbers.txt"
if [ -f "$BLACK" ]; then
  if [ "$MODE" = "message" ]; then
    HITS="$(grep -InEf "$BLACK" "$MSG_FILE" || true)"
  elif [ "$MODE" = "staged" ]; then
    HITS="$(git diff --cached -U0 | grep '^+' | grep -v '^+++' | grep -InEf "$BLACK" || true)"
  else
    HITS="$(grep -rInEf "$BLACK" . "${EXCLUDES[@]}" 2>/dev/null || true)"
  fi
  [ -n "$HITS" ] && report "黑名单（组合级数字）命中："$'\n'"$(echo "$HITS" | sed 's/^/      /' | head -20)"
else
  echo "  · 无 ${BLACK}（可选；建议把本地跑出的组合级数字逐行写进去）"
fi

if [ "$FAIL" -eq 0 ]; then
  echo "== 通过 ✓"
else
  echo "== 发现疑似泄露 ✗ —— 见 AGENTS.md §1：只写可公开的事实，实测数字留在仓库外"
fi
exit "$FAIL"
