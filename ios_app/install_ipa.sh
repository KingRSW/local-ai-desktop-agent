#!/bin/bash
# iPhoneClaw · iOS 一键安装脚本（免费 Apple ID / 个人团队）
# 用法：
#   1) iPhone 用数据线连 Mac（或在同一 Wi-Fi 下已配对无线调试）
#   2) 手机弹“信任此电脑”点信任
#   3) 终端运行：bash ios_app/install_ipa.sh
# 脚本会自动找到已连接的 iPhone/iPad 并把 IPA 装上去。
set -e
IPA="$(cd "$(dirname "$0")" && pwd)/build/IPhoneClaw.ipa"
[ -f "$IPA" ] || { echo "❌ 找不到 IPA: $IPA"; exit 1; }

echo "📦 IPA: $IPA"
# 取第一个已连接的真机 UDID（排除 Simulator）
UDID=$(xcrun xctrace list devices 2>/dev/null | grep -iE "iphone|ipad" | grep -v "Simulator" | grep -oE "\([A-F0-9-]{20,}\)$" | head -1 | tr -d '()')
if [ -z "$UDID" ]; then
  echo "❌ 没找到已连接的 iPhone/iPad。请用数据线连接并在手机上点“信任”。"
  echo "   已连接设备列表："
  xcrun xctrace list devices 2>/dev/null | grep -iE "iphone|ipad" | grep -v Simulator | head
  exit 1
fi
echo "📱 目标设备: $UDID"
xcrun devicectl device install app --device "$UDID" "$IPA"
echo "✅ 安装完成！手机主屏找“iPhoneClaw”图标打开即可。"
echo "   （免费账号签名的 App 有效期约 7 天，过期重跑本脚本即可。）"
