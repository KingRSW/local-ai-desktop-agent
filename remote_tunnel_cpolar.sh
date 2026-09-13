#!/usr/bin/env bash
# ============================================================
# 桌面员工 · 外网遥控一键隧道（cpolar 版，国内节点，关代理也能用）
# ------------------------------------------------------------
# 适用: 不想依赖 Clash 代理、或 Cloudflare 被墙时，用 cpolar 国内
#       节点转发，手机浏览器直接开，无需装 App。
# 前提: 1) 已装 cpolar  (brew install cpolar/cpolar/cpolar)
#       2) 已去 https://www.cpolar.cn 免费注册，并运行
#          cpolar authtoken <你的token>
# 用法:
#   bash remote_tunnel_cpolar.sh            # 自动生成随机遥控令牌
#   bash remote_tunnel_cpolar.sh 我的密码    # 指定令牌(好记)
# ============================================================
set -u

PROJ="$(cd "$(dirname "$0")" && pwd)"
PORT="${PORT:-8742}"

# 1) Python
PY="${DESKTOP_AGENT_PYTHON:-/Users/kingrsw/.workbuddy/binaries/python/envs/default/bin/python3}"
[ ! -x "$PY" ] && PY="$(command -v python3 || echo python3)"

# 2) 遥控令牌(server --token，防别人乱控)
TOKEN="${1:-${TOKEN:-}}"
if [ -z "$TOKEN" ]; then
  command -v openssl >/dev/null 2>&1 && TOKEN="$(openssl rand -hex 4)"
  [ -z "$TOKEN" ] && TOKEN="da-$(date +%s | tail -c 7)"
fi

# 3) 检查 cpolar 是否安装
if ! command -v cpolar >/dev/null 2>&1; then
  echo "❌ 未安装 cpolar。请先执行:"
  echo "   brew install cpolar/cpolar/cpolar"
  exit 1
fi

# 4) 检查 cpolar 是否已登录(authtoken)
if ! cpolar authtoken --list 2>/dev/null | grep -qi "authtoken"; then
  echo "❌ cpolar 尚未登录。请:"
  echo "   1) 打开 https://www.cpolar.cn 免费注册"
  echo "   2) 在后台『验证』页复制你的 Authtoken"
  echo "   3) 运行:  cpolar authtoken <你的Authtoken>"
  echo "   然后重新执行本脚本。"
  exit 1
fi

cleanup() {
  echo ""
  echo "🧹 关闭隧道与本地服务…"
  [ -n "${SRV_PID:-}" ] && kill "$SRV_PID" 2>/dev/null
  pkill -f "cpolar http" 2>/dev/null
  exit 0
}
trap cleanup EXIT INT TERM

# 5) 启动本地服务(后台)
echo "🚀 启动本地遥控服务 (127.0.0.1:$PORT, 令牌=$TOKEN)…"
"$PY" "$PROJ/agent/server.py" --host 127.0.0.1 --port "$PORT" --token "$TOKEN" &
SRV_PID=$!
sleep 2

cat > "$PROJ/remote_info_cpolar.txt" <<EOF
桌面员工 · 外网遥控(cpolar)连接信息
生成时间: $(date '+%Y-%m-%d %H:%M:%S')
连接令牌(token): $TOKEN
手机端步骤:
  1. 等终端出现 https://xxxx.cpolar.top (或 .cpolar.cn)
  2. 手机浏览器打开该地址
  3. 点右上 ⚙️，填上面的「连接令牌」
  4. 发送中文指令即可遥控 Mac
EOF

echo "============================================================"
echo "📱 手机在外网遥控(关代理也能用):"
echo "   1) 等下方出现  https://xxxx.cpolar.top"
echo "   2) 手机浏览器打开该地址"
echo "   3) 点右上 ⚙️，填连接令牌: $TOKEN"
echo "   连接信息已存: remote_info_cpolar.txt"
echo "   按 Ctrl+C 退出并关闭隧道"
echo "============================================================"

# 6) 前台跑 cpolar 隧道
cpolar http "127.0.0.1:$PORT" --loglevel info 2>&1 | tee /tmp/cpolar.log &
CPID=$!
sleep 8
DOMAIN="$(grep -oE "https://[a-z0-9-]+\.cpolar\.(top|cn|io)" /tmp/cpolar.log | head -1)"
[ -n "$DOMAIN" ] && echo "🌐 手机端公网地址: $DOMAIN"
wait "$CPID"
