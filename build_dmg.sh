#!/bin/bash
# 桌面员工 · 一键打包 .app + .dmg（启动器式，不打包 Python 运行时）
#
# 思路：.app 的主程序是一个极小的 bash 启动器，直接调用本机已装好的 venv python
# 运行 gui/app_gui.py。这样既得到双击即用的图形化 App，又避开了 py2app 把 Python
# 解释器打进 bundle 后在较新 macOS 上 ad-hoc 签名被 dyld 拒绝（CODESIGNING Invalid Page）的坑。
set -e

APP_NAME="桌面员工"
ROOT="$(cd "$(dirname "$0")" && pwd)"
PY="/Users/kingrsw/.workbuddy/binaries/python/envs/default/bin/python3"

cd "$ROOT"

# ① 生成图标（若已有则跳过）
if [ ! -f "gui/icon.icns" ]; then
  echo "① 生成图标…"
  "$PY" build/gen_icon.py
fi

# ② 清理旧产物
echo "② 清理旧产物…"
rm -rf "dist/$APP_NAME.app" dmg_root "$APP_NAME.dmg" 2>/dev/null || true

# ③ 组装 .app
echo "③ 组装 $APP_NAME.app…"
APP="dist/$APP_NAME.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/agent"

# 启动器脚本（名字与 App 一致，Dock/菜单栏显示更自然）
EXEC="$APP/Contents/MacOS/桌面员工"
cat > "$EXEC" <<'EOF'
#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
RES="$APP_ROOT/Contents/Resources"
PY="/Users/kingrsw/.workbuddy/binaries/python/envs/default/bin/python3"

if [ ! -x "$PY" ]; then
  osascript -e 'display dialog "未找到 Python 运行环境。\n请确认：/Users/kingrsw/.workbuddy/binaries/python/envs/default/bin/python3 是否存在。" buttons {"好"} default button "好"' 2>/dev/null
  exit 1
fi

export AGENT_BUNDLE_ROOT="$APP_ROOT"
cd "$RES" || exit 1
exec "$PY" "$RES/app_gui.py" "$@"
EOF
chmod +x "$EXEC"

# 资源：界面 + 引擎
cp gui/ui.html          "$APP/Contents/Resources/ui.html"
cp gui/app_gui.py       "$APP/Contents/Resources/app_gui.py"
cp gui/icon.icns        "$APP/Contents/Resources/AppIcon.icns"
cp agent/desktop_agent.py "$APP/Contents/Resources/agent/"
cp agent/ocr             "$APP/Contents/Resources/agent/"
cp agent/ocr.swift       "$APP/Contents/Resources/agent/"
cp agent/config.json     "$APP/Contents/Resources/agent/"
cp agent/config.example.json "$APP/Contents/Resources/agent/"
cp agent/templates.json "$APP/Contents/Resources/agent/"

# Info.plist
cat > "$APP/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>            <string>桌面员工</string>
  <key>CFBundleDisplayName</key>     <string>桌面员工</string>
  <key>CFBundleExecutable</key>      <string>桌面员工</string>
  <key>CFBundleIconFile</key>        <string>AppIcon</string>
  <key>CFBundleIdentifier</key>      <string>com.kingrsw.desktopployee</string>
  <key>CFBundleShortVersionString</key><string>1.0.0</string>
  <key>CFBundleVersion</key>         <string>1.0.0</string>
  <key>CFBundlePackageType</key>     <string>APPL</string>
  <key>LSMinimumSystemVersion</key>  <string>12.0</string>
  <key>NSHighResolutionCapable</key> <true/>
  <key>NSRequiresAquaSystemAppearance</key><false/>
  <key>NSAppleEventsUsageDescription</key>
    <string>桌面员工需要控制其他应用，才能替你完成任务。</string>
  <key>NSHumanReadableCopyright</key>
    <string>本地离线运行 · 数据不出本机</string>
</dict>
</plist>
EOF

echo "  PkgInfo"
printf 'APPL????' > "$APP/Contents/PkgInfo"

# ④ 本地 ad-hoc 签名（仅主程序 + 资源，不含 python 运行时，故不会触发 dylib 页签名问题）
echo "④ 签名…"
codesign --force --sign - "$APP" 2>&1 | tail -2 || true

# ⑤ 准备 dmg
echo "⑤ 准备 dmg 内容…"
rm -rf dmg_root
mkdir -p dmg_root
cp -R "$APP" dmg_root/
ln -s /Applications "dmg_root/Applications"
[ -f "使用说明.txt" ] && cp "使用说明.txt" dmg_root/ || true

# ⑥ 生成 dmg
echo "⑥ 生成 dmg…"
rm -f "$APP_NAME.dmg"
hdiutil create -volname "$APP_NAME" -srcfolder dmg_root -ov -format UDZO "$APP_NAME.dmg" 2>&1 | tail -3

SIZE=$(du -sh "$APP_NAME.dmg" | cut -f1)
echo ""
echo "✅ 完成"
echo "   App : $ROOT/$APP"
echo "   DMG : $ROOT/$APP_NAME.dmg  ($SIZE)"
