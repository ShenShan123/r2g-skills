#!/usr/bin/env bash
# r2g agent -- one-command install.
#
#   git clone <repo> && cd <repo> && bash install.sh
#
# 1. checks the machine (Linux, a Python >= 3.10 that can build a venv)
# 2. EDA toolchain via eda-install (ORFS + OpenROAD/Yosys, KLayout, the torch
#    venv for graph datasets). No root needed: without sudo it installs pre-built
#    conda packages onto the largest free volume. Already-installed tools are
#    detected and left alone.
# 3. verifies the platform this version supports (nangate45) is STRICT-READY
# 4. installs the agent (Python package + the pre-built web UI) into its own venv
# 5. puts the `r2g` command on your PATH (~/.local/bin)
# 6. asks which model to use and for its API key (stored only on this machine)
# 7. optionally runs the self-test: one small design RTL -> layout -> graph
#
# Options
#   --yes                 accept the toolchain plan without asking
#   --prefix DIR          where large things go (conda, venvs); default: picked for you
#   --skip-toolchain      the EDA tools are already set up; only install the agent
#   --provider NAME       deepseek|qwen|kimi|glm|doubao|openai|gemini|anthropic|ollama|custom
#   --model NAME          model name (default: the provider's suggestion)
#   --base-url URL        API endpoint (needed for custom)
#   --key-env VAR         read the API key from this environment variable
#   --no-config           do not ask about the model now (run `r2g config` later)
#   --selftest            run the self-test at the end without asking
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
YES=""; PREFIX=""; SKIP_TC=0; PROVIDER=""; MODEL=""; BASE_URL=""; KEY_ENV=""; NO_CONFIG=0; SELFTEST=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes|-y) YES="--yes"; shift ;;
    --prefix) PREFIX="$2"; shift 2 ;;
    --skip-toolchain) SKIP_TC=1; shift ;;
    --provider) PROVIDER="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --base-url) BASE_URL="$2"; shift 2 ;;
    --key-env) KEY_ENV="$2"; shift 2 ;;
    --no-config) NO_CONFIG=1; shift ;;
    --selftest) SELFTEST=1; shift ;;
    -h|--help) sed -n 2,31p "$0"; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[32m✓\033[0m %s\n' "$*"; }
die()  { printf '\n\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }
tty()  { [[ -t 0 && -t 1 ]]; }

# ---- 1. machine ---------------------------------------------------------------
say "1/7 检查机器"
[[ "$(uname -s)" == Linux ]] || die "r2g 目前只支持 Linux（EDA 工具链只提供 Linux 版本）"
ok "Linux $(uname -m)"
# a Python that can build a venv with pip; the first python3 on PATH may be a
# tool-bundled one without ensurepip (e.g. oss-cad-suite's), so try several
PY=""
for c in "${R2G_PYTHON:-}" python3.13 python3.12 python3.11 python3.10 python3 /opt/miniconda3/bin/python3 /usr/bin/python3; do
  [[ -n "$c" ]] || continue
  p="$(command -v "$c" 2>/dev/null || true)"; [[ -n "$p" ]] || continue
  if "$p" -c 'import sys, venv, ensurepip; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$p"; break
  fi
done
[[ -n "$PY" ]] || die "找不到能创建虚拟环境的 Python 3.10+（需要 venv 和 ensurepip 模块）。可用 R2G_PYTHON=/path/to/python3 指定。"
ok "Python $("$PY" -c 'import platform; print(platform.python_version())') ($PY)"
[[ -n "$PREFIX" ]] && mkdir -p "$PREFIX"
FREE_GB=$(df -Pk "${PREFIX:-$HOME}" 2>/dev/null | awk 'NR==2{print int($4/1048576)}' || true)
ok "磁盘可用约 ${FREE_GB} GB（工具链约需 15 GB）"

# ---- 2. toolchain -------------------------------------------------------------
if [[ $SKIP_TC -eq 0 ]]; then
  say "2/7 EDA 工具链（已安装的会被识别并跳过）"
  # a graph python pinned by an earlier install is the one to keep using
  PIN="$REPO/r2g-skills/def-graph/references/env.local.sh"
  if [[ -z "${R2G_GRAPH_PYTHON:-}" && -f "$PIN" ]]; then
    gp="$(sed -n 's/^export R2G_GRAPH_PYTHON="\{0,1\}\([^"]*\)"\{0,1\}$/\1/p' "$PIN" | head -1)"
    [[ -n "$gp" && -x "$gp" ]] && export R2G_GRAPH_PYTHON="$gp"
  fi
  # under set -e a failing bootstrap used to end the script with no message at
  # all (fresh-user test, 2026-10-23) -- say what failed and how to resume
  bash "$REPO/r2g-skills/eda-install/bootstrap.sh" $YES ${PREFIX:+--prefix "$PREFIX"} \
       --tiers core,frontend,klayout,platform_rules,graph --strict-platforms nangate45 \
    || die "EDA 工具链没有装完整（失败的那一项见上方 \"installer returned non-zero\" 附近的输出）。
  已下载的内容会保留，修好后重新运行同一条 bash install.sh 命令即可接着装。"
else
  say "2/7 跳过工具链安装（--skip-toolchain）"
fi

# ---- 3. verify ----------------------------------------------------------------
say "3/7 验证 nangate45 平台"
CHK="$(bash "$REPO/r2g-skills/signoff-loop/scripts/flow/check_env.sh" --platform nangate45 2>&1 || true)"
# exact: a line STARTING with the platform, second column STRICT-READY. A loose
# pattern matched "MISS strict capability: nangate45 (... strict_signoff_ready ...)"
# and passed an incomplete platform (fresh-user test, 2026-10-22).
if echo "$CHK" | grep -qE "^nangate45[[:space:]]+STRICT-READY"; then
  ok "nangate45：综合、布局布线、DRC、LVS、RCX 全部可用"
else
  echo "$CHK" | tail -25
  die "nangate45 还没有就绪（见上面的检查结果）。修好后重新运行 bash install.sh。"
fi

# ---- 4. agent -----------------------------------------------------------------
say "4/7 安装 r2g agent"
VENV="${R2G_AGENT_VENV:-${PREFIX:-$HOME/.r2g}/venv}"
[[ -x "$VENV/bin/python" ]] || "$PY" -m venv "$VENV"
"$VENV/bin/python" -m pip install -q --upgrade pip
"$VENV/bin/python" -m pip install -q -e "$REPO/agent"
"$VENV/bin/python" -c "import r2g_agent, fastapi, mcp" || die "agent 依赖安装不完整"
[[ -f "$REPO/agent/r2g_agent/web/dist/index.html" ]] || die "缺少预构建的界面（agent/r2g_agent/web/dist）"
ok "agent 已装入 $VENV"

# ---- 5. command ---------------------------------------------------------------
say "5/7 安装 r2g 命令"
mkdir -p "$HOME/.local/bin"
ln -sf "$VENV/bin/r2g" "$HOME/.local/bin/r2g"
ok "r2g -> $HOME/.local/bin/r2g"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *)
  echo "    注意：$HOME/.local/bin 不在 PATH 里。把下面这行加进 ~/.bashrc："
  echo '      export PATH="$HOME/.local/bin:$PATH"' ;;
esac
R2G="$VENV/bin/r2g"

# ---- 6. model -----------------------------------------------------------------
say "6/7 配置模型"
if [[ $NO_CONFIG -eq 1 ]]; then
  echo "    已跳过。之后运行：r2g config --provider deepseek --set-key（或在界面的「设置」页填写）"
elif [[ -n "$PROVIDER" ]]; then
  "$R2G" config --provider "$PROVIDER" ${MODEL:+--model "$MODEL"} ${BASE_URL:+--base-url "$BASE_URL"} \
         ${KEY_ENV:+--key-env "$KEY_ENV"} $( [[ -z "$KEY_ENV" ]] && tty && echo --set-key ) >/dev/null
  ok "模型：$PROVIDER"
elif tty; then
  echo "    可选：deepseek  qwen  kimi  glm  doubao  openai  gemini  anthropic  ollama  custom"
  read -r -p "    选择服务商 [deepseek]: " PROVIDER; PROVIDER="${PROVIDER:-deepseek}"
  read -r -p "    模型名（回车用默认）: " MODEL
  BU=""; [[ "$PROVIDER" == custom ]] && read -r -p "    接口地址（OpenAI 兼容，如 https://host/v1）: " BU
  "$R2G" config --provider "$PROVIDER" ${MODEL:+--model "$MODEL"} ${BU:+--base-url "$BU"} --set-key >/dev/null
  ok "模型：$PROVIDER（密钥只保存在 ~/.r2g/credentials.yaml，权限 600）"
  if "$R2G" check-model >/dev/null 2>&1; then ok "连接正常，支持工具调用"
  else echo "    ! 模型测试没通过，稍后可运行 r2g check-model 查看原因（不影响安装）"; fi
else
  echo "    非交互环境，已跳过。之后运行：r2g config --provider deepseek --set-key"
fi

# ---- 7. self-test -------------------------------------------------------------
say "7/7 自检"
if [[ -z "$SELFTEST" ]] && tty; then
  read -r -p "    现在跑一次自检吗？内置示例 counter8 从 RTL 到图数据集，约 3–5 分钟 [Y/n]: " a
  [[ "${a:-y}" =~ ^[Yy] ]] && SELFTEST=1
fi
if [[ -n "$SELFTEST" ]]; then
  "$R2G" selftest || die "自检没有通过（日志见 ~/.r2g/workspace/）"
  ok "自检通过"
else
  echo "    已跳过。之后运行：r2g selftest"
fi

cat <<EOF

$(printf '\033[1;32m')安装完成。$(printf '\033[0m')

  启动界面：   r2g ui            然后浏览器打开 http://127.0.0.1:8770
  远程服务器： 在你自己的电脑上运行  ssh -N -L 8770:127.0.0.1:8770 <用户>@<服务器>
  命令行：     r2g run --example counter8     r2g status     r2g chat
  接入 MCP：   r2g mcp   （Claude Code / Cursor 等客户端）
EOF
