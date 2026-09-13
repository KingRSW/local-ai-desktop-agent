#!/bin/bash
# iPhoneClaw · 安卓一键安装
# 用法：
#   1) 手机打开「设置→关于手机→连续点版本号」开启开发者模式
#   2) 开发者选项里打开「USB 调试」
#   3) 数据线连 Mac，手机弹「允许 USB 调试」点允许
#   4) 终端运行：bash android_app/install_apk.sh
set -e
APK="$(cd "$(dirname "$0")" && pwd)/IPhoneClaw.apk"
[ -f "$APK" ] || { echo "❌ 找不到 APK: $APK（先跑 bash android_app/build_apk.sh）"; exit 1; }
ADB="${ANDROID_HOME:-/Users/kingrsw/Library/Android/sdk}/platform-tools/adb"
echo "📦 APK: $APK"
echo "📱 已连接设备："
"$ADB" devices | grep -v "List" | grep "device$" || { echo "❌ 没检测到安卓设备，请检查 USB 调试。"; exit 1; }
"$ADB" install -r "$APK"
echo "✅ 安装完成！手机主屏找「iPhoneClaw」打开，首次会让你填 Mac 服务器地址。"
