#!/bin/bash
# iPhoneClaw · 安卓 APK 一键构建（无需 Gradle，直接用 Android SDK 命令行工具）
# 依赖：ANDROID_HOME 已设置（本机 /Users/kingrsw/Library/Android/sdk）
set -e
SDK="${ANDROID_HOME:-/Users/kingrsw/Library/Android/sdk}"
BT="$SDK/build-tools/36.0.0"
PLAT="$SDK/platforms/android-36"
APP="$(cd "$(dirname "$0")" && pwd)"
PKG_DIR="$APP/src/com/kingrsw/iphoneclaw"
WORK="$(mktemp -d)"
OBJ="$WORK/obj"; GEN="$WORK/gen"; RESZ="$WORK/res.zip"; DEXDIR="$WORK/dex"
mkdir -p "$OBJ" "$GEN" "$DEXDIR"
echo "📁 工作目录: $WORK"

# 1) 编译资源
echo "🔨 编译资源..."
"$BT/aapt2" compile --dir "$APP/res" -o "$RESZ"

# 2) 链接并生成 R.java
echo "🔨 链接资源 + 生成 R.java..."
"$BT/aapt2" link -o "$WORK/app-unsigned.apk" -I "$PLAT/android.jar" \
  --manifest "$APP/AndroidManifest.xml" -R "$RESZ" --java "$GEN" \
  --auto-add-overlay --no-version-vectors

# 3) 编译 Java（含 R.java）
echo "🔨 编译 Java..."
javac --release 17 -cp "$PLAT/android.jar" -d "$OBJ" \
  $(find "$PKG_DIR" -name "*.java") \
  "$GEN/com/kingrsw/iphoneclaw/R.java"

# 4) 转 DEX
echo "🔨 生成 classes.dex..."
jar cf "$WORK/classes.jar" -C "$OBJ" .
"$BT/d8" --release --lib "$PLAT/android.jar" --output "$DEXDIR" "$WORK/classes.jar"

# 5) 塞入 classes.dex
echo "🔨 写入 classes.dex..."
python3 - "$WORK/app-unsigned.apk" "$DEXDIR/classes.dex" <<'PY'
import sys, zipfile
apk, dex = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(apk, "a") as z:
    if "classes.dex" not in z.namelist():
        z.write(dex, "classes.dex")
print("   dex 已加入")
PY

# 6) 对齐
echo "🔨 zipalign..."
"$BT/zipalign" -p 4 "$WORK/app-unsigned.apk" "$WORK/app-aligned.apk"

# 7) 签名（自动建 debug keystore）
KEY="$HOME/.android/debug.keystore"
if [ ! -f "$KEY" ]; then
  echo "🔑 生成 debug 签名..."
  keytool -genkey -v -keystore "$KEY" -storepass android -alias androiddebugkey \
    -keypass android -keyalg RSA -keysize 2048 -validity 10000 \
    -dname "CN=Android Debug,O=Android,C=US" >/dev/null 2>&1
fi
echo "🔏 签名 APK..."
"$BT/apksigner" sign --ks "$KEY" --ks-pass pass:android --key-pass pass:android \
  --out "$APP/IPhoneClaw.apk" "$WORK/app-aligned.apk"

ls -lh "$APP/IPhoneClaw.apk"
echo "✅ 完成: $APP/IPhoneClaw.apk"
echo "   安装到手机: adb install -r $APP/IPhoneClaw.apk"
rm -rf "$WORK"
