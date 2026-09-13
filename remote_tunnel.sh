#!/usr/bin/env bash
# ============================================================
# 桌面员工 · 外网遥控一键隧道
# ------------------------------------------------------------
# 用途: 手机不在同一 WiFi 时，把 Mac 本地服务经 Cloudflare
#       免费隧道转发到公网(自动 HTTPS)，手机在任何有网处都能遥控。
# 前提: Mac 能上外网 + 手机能上外网(蜂窝/任意 WiFi 均可)。
# 安全: 强制生成连接令牌(token)，防止他人乱控你的电脑。
#
# 用法:
#   bash remote_tunnel.sh            # 自动生成随机令牌
#   bash remote_tunnel.sh 我的密码   # 指定令牌(方便记忆)
#   TOKEN=xxx bash remote_tunnel.sh  # 或用环境变量
# ============================================================
set -u

PROJ="$(cd "$(dirname "$0")" && pwd)"
PORT="${PORT:-8742}"
HOST=127.0.0.1   # 仅本机，公网经 cloudflared 转发，更安全

# 1) 选 Python: 优先 venv，回退 python3
PY="${DESKTOP_AGENT_PYTHON:-/Users/kingrsw/.workbuddy/binaries/python/envs/default/bin/python3}"
if [ ! -x "$PY" ]; then PY="$(command -v python3 || echo python3)"; fi

# 2) 选令牌
TOKEN="${1:-${TOKEN:-}}"
if [ -z "$TOKEN" ]; then
  if command -v openssl >/dev/null 2>&1; then
    TOKEN="$(openssl rand -hex 4)"   # 8 位十六进制
  else
    TOKEN="da-$(date +%s | tail -c 7)"
  fi
fi

# 3) 检查 cloudflared
if ! command -v cloudflared >/dev/null 2>&1; then
  echo "❌ 未找到 cloudflared。请先安装:"
  echo "   brew install cloudflared"
  exit 1
fi

cleanup() {
  echo ""
  echo "🧹 正在关闭隧道与本地服务…"
  [ -n "${SRV_PID:-}" ] && kill "$SRV_PID" 2>/dev/null
  [ -n "${CF_PID:-}" ] && kill "$CF_PID" 2>/dev/null
  exit 0
}
trap cleanup EXIT INT TERM

# 4) 启动本地服务(后台, 仅本机 + 强制令牌)
echo "🚀 启动本地遥控服务 (127.0.0.1:$PORT, 令牌=$TOKEN)…"
"$PY" "$PROJ/agent/server.py" --host "$HOST" --port "$PORT" --token "$TOKEN" &
SRV_PID=$!
sleep 2

# 5) 把连接信息写到文件，方便回看
cat > "$PROJ/remote_info.txt" <<EOF
桌面员工 · 外网遥控连接信息
生成时间: $(date '+%Y-%m-%d %H:%M:%S')
连接令牌(token): $TOKEN
手机端步骤:
  1. 等终端出现 https://xxxx.trycloudflare.com
  2. 手机浏览器打开该地址
  3. 点右上 ⚙️ 设置，填上面的「连接令牌」
  4. 发送中文指令即可遥控 Mac
EOF

echo "============================================================"
echo "📱 手机在外网遥控步骤:"
echo "   1) 等下方出现  https://xxxx.trycloudflare.com"
echo "   2) 手机浏览器打开该地址"
echo "   3) 点右上 ⚙️，填连接令牌: $TOKEN"
echo "   4) 发送指令即可遥控 Mac"
echo "   连接信息已存: remote_info.txt"
echo "   按 Ctrl+C 退出并关闭隧道"
echo "============================================================"

# 5.5) 关键: 让隧道数据面走 Clash 代理(海外节点)，绕过网络对 argotunnel 的封锁
#      实测直连数据面会被墙(hard_fail)，借 Clash 代理即可全通；
#      但若 Clash 代理端口没开，硬塞代理反而会连不上。这里只监听才启用代理。
CLASH_PROXY_URL=""
if [ -n "${CLASH_PROXY:-}" ]; then
  # 用户显式指定，直接用
  CLASH_PROXY_URL="$CLASH_PROXY"
  echo "✅ 使用指定代理: $CLASH_PROXY_URL"
elif [ "${CLASH_PROXY_OFF:-}" != "1" ]; then
  # 自动探测 Clash 代理端口
  for p in ${CLASH_PORT:-7890} 7891 7892 7893 7899 8888 1080 2080; do
    if (exec 3<>/dev/tcp/127.0.0.1/$p) 2>/dev/null; then
      exec 3>&- 2>/dev/null
      CLASH_PROXY_URL="http://127.0.0.1:$p"
      echo "✅ 检测到 Clash 代理端口: $p"
      break
    fi
  done
fi
if [ -n "$CLASH_PROXY_URL" ]; then
  export HTTPS_PROXY="$CLASH_PROXY_URL" HTTP_PROXY="$CLASH_PROXY_URL"
  echo "🌐 隧道数据面走 Clash 代理，绕过网络对 argotunnel 的封锁"
else
  echo "ℹ️ 未检测到 Clash 代理，已回退直连；若数据面 hard_fail 请打开 Clash 系统代理或设 CLASH_PROXY"
fi

# 6) 前台跑隧道(域名会实时打印出来)
cloudflared tunnel --url "http://localhost:$PORT" --loglevel info 2>&1 &
CF_PID=$!
wait "$CF_PID"
