#!/bin/bash
# iPhoneClaw · iOS IPA 一键重打（需 Xcode + 已登录 Apple ID + 一台已连真机）
# 用法：bash ios_app/build_ipa.sh
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PROJ=ios_app/DesktopAgent.xcodeproj
TEAM=KDX2FPABY9

# 自动取第一台已连真机 UDID（排除 Simulator）
UDID=$(xcrun xctrace list devices 2>/dev/null | grep -iE "iphone|ipad" | grep -v "Simulator" | grep -oE "\([A-F0-9-]{20,}\)$" | head -1 | tr -d '()')
if [ -z "$UDID" ]; then
  echo "❌ 未检测到已连接真机。请用数据线连 iPhone/iPad 并在手机点“信任”。"
  exit 1
fi
echo "📱 设备: $UDID"

rm -rf /tmp/da_build
xcodebuild archive \
  -project "$PROJ" -scheme DesktopAgent -configuration Release \
  -destination "platform=iOS,id=$UDID" \
  -archivePath /tmp/da_build/DesktopAgent.xcarchive \
  DEVELOPMENT_TEAM=$TEAM CODE_SIGN_STYLE=Automatic -allowProvisioningUpdates 2>&1 | tail -4
echo "📦 导出 IPA..."
rm -rf /tmp/da_ipa
xcodebuild -exportArchive \
  -archivePath /tmp/da_build/DesktopAgent.xcarchive \
  -exportPath /tmp/da_ipa \
  -exportOptionsPlist ios_app/ExportOptions.plist -allowProvisioningUpdates 2>&1 | tail -4
mkdir -p ios_app/build
cp /tmp/da_ipa/DesktopAgent.ipa ios_app/build/IPhoneClaw.ipa
ls -lh ios_app/build/IPhoneClaw.ipa
echo "✅ 完成：ios_app/build/IPhoneClaw.ipa"
echo "   安装：bash ios_app/install_ipa.sh"
