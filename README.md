# 桌面员工 · 本地 AI 桌面代理（蓝海项目 MVP）

> 用一句中文下达指令，你自己的本地大模型听懂后，直接操控鼠标键盘把活干完。
> 不上云、不泄露、Mac / Windows 都能跑。

## 为什么是蓝海（几乎没有对手）
- 云端 AI（ChatGPT / Coze / 文心）：碰不到真实桌面，只能给建议。
- 微软 Power Automate / 苹果快捷指令：技术门槛高、英文、要自己搭流程。
- 国内 RPA（影刀 / UiBot）：贵、偏企业、要写流程，缺自然语言层。
- 本项目的组合（Flutter 液态玻璃 UI ＋ Python 自动化引擎 ＋ 本地 Ollama(qwen) ＋ 中文场景理解）市面上几乎独家。

## 目录
- `gui/app_gui.py`：**图形界面主程序**（pywebview + 液态玻璃 UI）。
- `gui/ui.html`：界面（可单独用浏览器打开预览效果）。
- `agent/desktop_agent.py`：执行引擎（三条通道：微信键盘流 / 本地拆步 / 视觉闭环）。
- `agent/server.py`：Mac 端遥控服务（HTTP + 手机浏览器控制台）。
- `agent/console.html`：手机浏览器响应式控制台。
- `agent/ocr.swift` + `agent/ocr`：macOS 系统 Vision OCR（认字定位，离线免费）。
- `ios_app/DesktopAgent.xcodeproj`：SwiftUI iOS 原生遥控器 App。
- `build_dmg.sh`：一键打包成 `.app` 与 `.dmg`（启动器式 .app，直接调用本机 venv Python，绕开 Python 解释器 dylib 签名坑）。
- `index.html`：获客落地页（可直接双击打开 / 部署）。

## 图形界面（推荐用法）
```bash
# 源码直接跑
python3 gui/app_gui.py

# 一键打包 .app + .dmg
bash build_dmg.sh          # 产物：桌面员工.dmg
```
界面里能做的事：输入中文指令直接执行、仅预览不操作、实时日志、
环境自检（模型服务 / OCR / 辅助功能权限 / 屏幕录制权限一键跳转设置）、
切换视觉模型、调步进间隔、执行后回显屏幕截图、随时停止。

## 手机版 · 遥控 Mac（两种形态）

### ① 手机浏览器控制台（最快，不用装 App）
在 Mac 端启动服务：
```bash
cd agent
python3 server.py --port 8742
```
手机连**同一 WiFi**，浏览器打开控制台里打印的地址：
```
http://<你的 Mac 局域网IP>:8742
```
默认不需要 token；需要在公共 WiFi 防别人乱控时，加 `--token xxx123`。

### ② iOS 原生 App（iPhoneClaw，SwiftUI）
App 已改名为 **iPhoneClaw**（bundle id `com.kingrsw.iphoneclaw`）。
- **最简单安装**：数据线连 Mac，手机点「信任」，然后 `bash ios_app/install_ipa.sh` 一键装（免费 Apple ID 即可，有效期约 7 天，过期重跑）。
- 或自己用 Xcode 打开 `ios_app/DesktopAgent.xcodeproj` → 选 Team → Run。
- App 内「连接设置」填 Mac 的地址 + token（Simulator 直接连 127.0.0.1）。
- 想重新出包：`bash ios_app/build_ipa.sh`（需 Xcode + 已登录 Apple ID）。

### ③ 安卓原生 App（iPhoneClaw，WebView 控制台）
安卓版是同一个遥控器的极简封装：App 内填 Mac 服务器地址，加载控制台网页（和手机浏览器控制台一模一样）。
- **出包**：`bash android_app/build_apk.sh`（用本机 Android SDK 命令行工具，无需 Gradle，产出 `android_app/IPhoneClaw.apk`）
- **安装**：手机开「开发者选项→USB 调试」，连 Mac，`bash android_app/install_apk.sh`
- 首次打开填 Mac 地址（外网隧道 `https://xxxx.trycloudflare.com` 或局域网 `http://192.168.x.x:8742`），网页内 ⚙️ 填 token。
- 最低 Android 7.0（API 24），允许 http 局域网明文（已配 network_security_config）。

### 手机端用法要点
- 指令要**具体、可拆步**：`打开微信 搜索 张三 发消息 在吗`
- 屏幕上必须**先能看到目标文字**，OCR 才能精准命中；看不到的目标会直接停止，不会乱点。
- 复杂自然语言（如「帮我把这事办了」）会走弱视觉模型兜底，可能点错，尽量用明确步骤。

## 在外面遥控（外网 / 不在同一 WiFi）

手机和 Mac 不在同一局域网时，直连 `192.168.x.x` 会失败。本质要求：**Mac 能上外网 + 手机能上外网**（蜂窝、任意陌生 WiFi 都行）。三种办法：

### 方案① cloudflared 免费隧道（推荐，无需服务器、自动 HTTPS）
本机已装 `cloudflared`。一键把本地服务转发到公网：
```bash
bash remote_tunnel.sh            # 自动生成随机令牌
bash remote_tunnel.sh 我的密码    # 或指定令牌(好记)
```
终端会打印一个 `https://xxxx.trycloudflare.com` 公网地址。手机浏览器打开它 → 点右上 ⚙️ → 填「连接令牌」→ 发指令即可。
连接信息同时存到 `remote_info.txt`。**退出按 Ctrl+C 会同时关掉隧道和本地服务。**

> ⚠️ 安全铁律：外网必须带 `--token`（脚本已强制）。否则任何人拿到地址就能远程操控你的 Mac 桌面。
> 🌐 国内网络：直连 Cloudflare 隧道数据面会被墙，脚本已自动让隧道走本机 Clash 代理（默认 `127.0.0.1:7890`）绕过封锁（实测 `hard_fail=false` 可用）。请确保 Clash 已开启；端口非 7890 时：`CLASH_PORT=端口 bash remote_tunnel.sh` 或 `CLASH_PROXY='http://127.0.0.1:端口' bash remote_tunnel.sh`。

### 方案② ZeroTier 虚拟局域网（更私密、固定地址）
手机和 Mac 加入同一个 ZeroTier 网络后，就像一直在同一 WiFi，可直接用分配的虚拟 IP 连接，不经第三方转发。
```bash
brew install zerotier-one        # Mac 端
# 手机装 ZeroTier One App，两端加入同一 Network ID
```
组好后手机端直接填 ZeroTier 给 Mac 的虚拟 IP（如 `10.x.x.x:8742`）。

### 方案③ 手机给 Mac 开热点（应急）
若 Mac 本身断网（比如在户外），用手机开「个人热点」让 Mac 连上，Mac 有了网就能走方案①或②。纯离线无法遥控——遥控本质是远程通信，两端都得有网。

### 方案④ cpolar 隧道（国内节点，关代理也能用，不依赖 Clash）
若不想依赖 Clash 代理、或某天 Cloudflare 被墙导致 `cloudflared` 直连不稳，可改用 **cpolar**（国内节点，注册免费，手机浏览器直接开，无需装 App）：
```bash
brew install cpolar/cpolar/cpolar          # 1. 安装
# 2. 去 https://www.cpolar.cn 免费注册，复制 Authtoken
cpolar authtoken <你的Authtoken>            # 3. 登录
bash remote_tunnel_cpolar.sh               # 4. 启动(自动生成遥控令牌)
```
终端会打印 `https://xxxx.cpolar.top` 公网地址，手机浏览器打开 → ⚙️ 填令牌即可。
> 注：cpolar 免费版为随机域名、有流量/时长限制；长期固定域名需付费。

## 命令行用法（调试用）
```bash
cd agent
python3 desktop_agent.py "打开微信 给文件传输助手 发条消息：在吗"
python3 desktop_agent.py "打开Safari 按 command+t 输入 知乎 按回车"
```

## 快速体验
1. 引擎依赖：
   ```bash
   cd agent
   pip install -r requirements.txt
   ```
2. 准备本地模型（已有 Ollama 可跳过）：
   ```bash
   ollama run gemma4
   # 也可换：ollama run qwen2.5:7b / qwen3.5:4b / deepseek-r1:7b
   ```
3. 跑一条指令（Ollama 没开也会自动降级为规则模式）：
```bash
python desktop_agent.py "打开浏览器搜一下深圳天气并截图"
```
   > 提示：搜索类任务现在会优先用 `open_url` 直接打开带关键词的搜索页（如 `https://www.baidu.com/s?wd=...`），避免“只打开首页、没填搜索词”的问题。

## 下一步（做成完整产品）
- 用 Flutter 把 `local_agent.py` 的能力包成漂亮的跨端 App（你已有 PC 遥控器基础）。
- 加「指令市场」：用户分享/购买中文自动化模板。
- 接等候名单邮箱，做内测放量。

## 从源码运行（开发者 / 贡献者）
```bash
git clone <本仓库地址>
cd local-ai-desktop-agent
python3 -m venv venv && source venv/bin/activate
pip install -r agent/requirements.txt
# 编译 OCR 引擎(macOS, 需 Xcode 命令行工具): 仓库只提交源码 agent/ocr.swift
swiftc -O -o agent/ocr agent/ocr.swift
# 启动遥控服务(局域网)
python agent/server.py --port 8742
# 或开公网隧道(见上文「在外面遥控」)
bash remote_tunnel.sh
```
> ⚠️ 安全铁律：外网（quick tunnel / cpolar）务必加 `--token 你的令牌`，否则任何人拿到地址都能远程操控你的电脑。
