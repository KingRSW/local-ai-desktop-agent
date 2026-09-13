#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
桌面员工 · 本地 AI 桌面代理 —— MVP 引擎 v2
==========================================
用一句中文下达指令，本地 Ollama(qwen / 可换模型) 把指令拆成可执行动作计划，
再由 Python(pyautogui / webbrowser / 文件操作) 在真实桌面上执行。
可选接入 OpenClaw 视觉后端，支持"按屏幕上的文字点击"。

特点：
  - 全程本地，不联网、不上传任何数据
  - 支持 mac / win 双端
  - 自然语言 -> 动作计划 -> 执行
  - 动作库覆盖常见桌面任务；超能力范围时礼貌提示，不崩溃
  - 模型可换（config.model 填任意 Ollama 模型）
  - 可插拔 OpenClaw 视觉后端

运行前准备：
  pip install -r requirements.txt
  ollama pull gemma4            # 默认模型，也可换 qwen2.5:7b / qwen3.5:4b / deepseek-r1:7b 等
  python local_agent.py "打开浏览器搜一下深圳天气并截图" --dry-run   # 先看计划
  python local_agent.py "..."                                            # 真执行
"""

import argparse
import json
import os
import sys
import time
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote

# ---------- 动作说明（给模型当"字典"，只能从这几种里选） ----------
ACTION_DOCS = """
可用动作(只能从下面选,不要发明新动作,不要编造字段):
{"action":"open_app","name":"应用名"}
{"action":"open_url","url":"https://...","note":"中文关键词必须 URL 编码"}
{"action":"type","text":"要输入的内容"}
{"action":"hotkey","keys":["ctrl","t"]}
{"action":"key_press","key":"enter"}
{"action":"click","x":100,"y":200}
{"action":"click_text","text":"屏幕上要点击的文字(需要视觉后端 OpenClaw)"}
{"action":"move","x":100,"y":200}
{"action":"scroll","amount":-3}
{"action":"wait","seconds":2}
{"action":"screenshot","name":"文件名"}
{"action":"file_rename","folder":"~/Downloads","pattern":"*.png","prefix":"图"}
{"action":"file_move","src":"源路径","dst":"目标路径"}
{"action":"clipboard_copy"} / {"action":"clipboard_paste"}
{"action":"window_maximize"} / {"action":"window_close"}
未知任务请用上面最接近的动作组合完成。不要输出任何解释，只输出 JSON 数组。
"""

# ---------- 我能做什么（能力边界，给用户看的） ----------
CAPABILITIES = [
    "打开 / 切换任意 App",
    "用浏览器打开网址并搜索（中文自动编码）",
    "模拟键盘输入、快捷键、单键",
    "鼠标点击坐标、移动、滚动",
    "屏幕截图并保存",
    "批量重命名 / 移动文件",
    "剪贴板复制粘贴",
    "窗口最大化 / 关闭",
    "（开启 OpenClaw 视觉后端后）按屏幕上的文字点击",
]

# ---------- 预设任务模板（一键可用，降低自然语言门槛） ----------
TEMPLATES = [
    {"id": "search_shot", "title": "百度搜词并截图",
     "instruction": "打开浏览器搜一下 深圳天气 并截图",
     "desc": "搜索结果页自动截图留存"},
    {"id": "clean_desktop", "title": "整理桌面截图",
     "instruction": "把桌面上所有截图文件移动到 桌面/截图归档 文件夹",
     "desc": "一键归拢零散截图"},
    {"id": "rename_dl", "title": "批量重命名下载图片",
     "instruction": "把下载文件夹里所有 png 图片按 图001 图002 重命名",
     "desc": "下载图批量规范命名"},
    {"id": "open_wechat", "title": "打开微信发消息",
     "instruction": "打开微信 并点击 文件传输助手",
     "desc": "（需视觉后端）定位并点开会话"},
    {"id": "max_browser", "title": "浏览器全屏看新闻",
     "instruction": "打开浏览器搜 今日新闻 然后把窗口最大化",
     "desc": "边看边记录"},
]


# ---------- 配置 ----------
CONFIG_DEFAULT = {
    "ollama_host": "http://localhost:11434",
    "model": "qwen2.5:7b",      # 可换: qwen3.5:4b / deepseek-r1:7b / qwen2.5:14b 等
    "safe_mode": True,          # 危险动作需要 y 确认
    "step_delay": 0.8,          # 每步间隔(秒)，防误触
    "screenshot_dir": "screenshots",
    "vision_backend": "pyautogui",   # 或 "openclaw"
    "openclaw_cmd": "openclaw",      # 你本地 OpenClaw 的调用命令/脚本，自行填写
}


def load_config(path="config.json"):
    cfg = dict(CONFIG_DEFAULT)
    if os.path.exists(path):
        try:
            cfg.update(json.load(open(path, encoding="utf-8")))
        except Exception:
            pass
    os.makedirs(os.path.expanduser(cfg.get("screenshot_dir", "screenshots")), exist_ok=True)
    return cfg


# ---------- 调用本地 Ollama（可换模型） ----------
def ask_ollama(cfg, instruction):
    """把中文指令转成动作计划(JSON)。失败返回 None。"""
    try:
        import urllib.request
    except Exception:
        return None

    sys_prompt = (
        "你是一个桌面自动化规划器。用户用中文描述一个电脑操作任务，"
        "请只输出一个 JSON 数组，每个元素是一个动作对象。\n" + ACTION_DOCS +
        "\n重要规则：\n"
        "1. 搜索类任务（百度/必应/谷歌）优先使用 open_url 直接打开带搜索关键词的 URL，"
        "不要先打开首页再用 type 输入。例如搜索'深圳天气'应输出："
        "{\"action\":\"open_url\",\"url\":\"https://www.baidu.com/s?wd=%E6%B7%B1%E5%9C%B3%E5%A4%A9%E6%B0%94\"}\n"
        "2. 只输出 JSON 数组，不要解释、不要 markdown 代码块。\n"
        "3. 如果任务超出能力，输出 [] 空数组，不要编造动作。"
    )
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": instruction},
        ],
        "stream": False,
    }
    try:
        req = urllib.request.Request(
            cfg["ollama_host"].rstrip("/") + "/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["message"]["content"]
        start = content.find("[")
        end = content.rfind("]")
        if start != -1 and end != -1:
            return json.loads(content[start:end + 1])
    except Exception as e:
        print(f"  [Ollama 不可用，降级规则模式] {e}")
    return None


# ---------- 规则兜底（无模型也能跑常见指令） ----------
def _extract_search_kw(instruction):
    """从'搜一下 XX 并...'/'查一下 XX' 中提取干净搜索词(剔除截图/保存等干扰后缀)。"""
    import re
    stop = r"(?:并|，|,|。|截图|截屏|截个图|截张图|保存到|存到|存进|到桌面|然后|再|顺便|之后)"
    for pat in (
        r"搜(?:索|一下|索一下)?\s*(.+?)(?:" + stop + r"|\s*$)",
        r"查一下\s*(.+?)(?:" + stop + r"|\s*$)",
        r"查询\s*(.+?)(?:" + stop + r"|\s*$)",
    ):
        m = re.search(pat, instruction)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return ""


def _snapshot_filename(instruction):
    """根据指令关键词生成截图文件名。"""
    import re
    kw = _extract_search_kw(instruction)
    if kw:
        return re.sub(r'[\\/:*?"<>|]', "_", kw)
    return "结果"


def post_process_plan(plan, instruction):
    """系统层兜底修正：模型输出不稳定时强制补齐/修正关键动作。"""
    if not isinstance(plan, list):
        plan = []

    # 1) 搜索任务强制直接打开搜索URL（不再依赖模型输出，确保关键词正确）
    WANTS_SEARCH = ("搜" in instruction) or ("查询" in instruction) or ("查一下" in instruction)
    if WANTS_SEARCH:
        kw = _extract_search_kw(instruction)
        if kw:
            search_url = "https://www.baidu.com/s?wd=" + quote(kw)
            has_url = False
            for s in plan:
                if s.get("action") == "open_url":
                    s["url"] = search_url
                    has_url = True
            if not has_url:
                plan.insert(0, {"action": "open_url", "url": search_url})
            # 删掉多余的 type/key_press，避免中文输入失败
            plan = [s for s in plan if s.get("action") not in ("type", "key_press")]

    # 2) 截图任务强制补 screenshot
    wants_shot = any(k in instruction for k in ("截图", "截屏", "截个图", "截张图"))
    has_shot = any(s.get("action") == "screenshot" for s in plan)
    if wants_shot and not has_shot:
        plan.append({"action": "screenshot", "name": _snapshot_filename(instruction)})

    # 3) 搜索+截图类任务在截图前加一个等待，让页面加载完成
    if wants_shot and any(s.get("action") == "open_url" for s in plan):
        # 在最后一个 open_url 之后、screenshot 之前插入 wait
        new_plan = []
        inserted_wait = False
        for s in plan:
            new_plan.append(s)
            if s.get("action") == "open_url" and not inserted_wait:
                new_plan.append({"action": "wait", "seconds": 3})
                inserted_wait = True
        plan = new_plan

    # 4) 截图落盘位置：指令说"桌面"就存桌面
    for s in plan:
        if s.get("action") == "screenshot":
            if "desktop" not in s:
                s["desktop"] = ("桌面" in instruction)

    # 5) 合并相邻的 wait，避免重复等待
    merged = []
    i = 0
    while i < len(plan):
        s = plan[i]
        if s.get("action") == "wait":
            tot = float(s.get("seconds", 0))
            j = i + 1
            while j < len(plan) and plan[j].get("action") == "wait":
                tot += float(plan[j].get("seconds", 0))
                j += 1
            merged.append({"action": "wait", "seconds": tot})
            i = j
        else:
            merged.append(s)
            i += 1
    plan = merged

    return plan


def rule_plan(instruction):
    plan = []
    name = "记事本" if sys.platform.startswith("win") else "文本编辑"
    if any(k in instruction for k in ("浏览器", "上网", "搜索", "搜")):
        plan.append({"action": "open_app", "name": "浏览器"})
        kw = _extract_search_kw(instruction)
        if kw:
            plan.append({"action": "open_url", "url": "https://www.baidu.com/s?wd=" + quote(kw)})
    if "截图" in instruction or "截个图" in instruction:
        plan.append({"action": "screenshot", "name": _snapshot_filename(instruction)})
    if "最大化" in instruction or "全屏" in instruction:
        plan.append({"action": "window_maximize"})
    if "重命名" in instruction or "批量命名" in instruction:
        folder = "~/Downloads"
        if "桌面" in instruction:
            folder = "~/Desktop"
        plan.append({"action": "file_rename", "folder": folder, "pattern": "*.png", "prefix": "图"})
    if "移动" in instruction or "归档" in instruction or "整理" in instruction:
        plan.append({"action": "file_move", "src": "~/Desktop/*.png", "dst": "~/Desktop/截图归档"})
    if not plan:
        plan.append({"action": "open_app", "name": name})
    plan.append({"action": "wait", "seconds": 1})
    return plan


# ---------- 调用 OpenClaw 视觉后端（占位适配，按你实际接口调） ----------
def run_openclaw(cfg, task):
    """把自然语言任务交给本地 OpenClaw 视觉执行。需自行配置 openclaw_cmd。"""
    print(f"  🔮 调用 OpenClaw 视觉执行: {task}")
    try:
        r = subprocess.run([cfg["openclaw_cmd"], task], capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            print(f"     ⚠️ OpenClaw 返回错误: {r.stderr.strip() or '未知'}")
        return r.returncode == 0
    except Exception as e:
        print(f"     ⚠️ 未找到 OpenClaw 命令({cfg['openclaw_cmd']})，请检查 config.openclaw_cmd。错误: {e}")
        return False


# ---------- 执行器 ----------
KNOWN_ACTIONS = {
    "open_app", "open_url", "type", "hotkey", "key_press", "click",
    "click_text", "move", "scroll", "wait", "screenshot", "file_rename",
    "file_move", "clipboard_copy", "clipboard_paste", "window_maximize", "window_close",
}


def execute(cfg, plan):
    try:
        import pyautogui
        pyautogui.FAILSAFE = True
    except Exception as e:
        print(f"[缺少依赖] 请先: pip install pyautogui  -> {e}")
        return

    import webbrowser

    for i, step in enumerate(plan, 1):
        act = step.get("action")
        print(f"  ▶ 步骤{i}: {act}  { {k: v for k, v in step.items() if k != 'action'} }")
        if act not in KNOWN_ACTIONS:
            print(f"     ⚠️ 跳过：暂不支持的动作「{act}」（我目前能做：{', '.join(CAPABILITIES)}）")
            time.sleep(cfg.get("step_delay", 0.8))
            continue
        try:
            if act == "open_app":
                name = step.get("name", "")
                if sys.platform.startswith("win"):
                    os.system(f"start {name}")
                else:
                    os.system(f"open -a '{name}'")
            elif act == "open_url":
                url = step.get("url", "https://www.baidu.com")
                if any(ord(c) > 127 for c in url):
                    url = quote(url, safe='/:?=&.%')
                webbrowser.open(url)
                # 把浏览器提到最前，避免被其他 App 盖住导致截图截错
                try:
                    subprocess.run(["osascript", "-e", 'tell application "Safari" to activate'],
                                     capture_output=True, timeout=5)
                except Exception:
                    pass
            elif act == "type":
                text = step.get("text", "")
                if any(ord(c) > 127 for c in text):
                    import pyperclip
                    pyperclip.copy(text)
                    pk = 'command' if sys.platform.startswith('darwin') else 'ctrl'
                    pyautogui.hotkey(pk, 'v')
                else:
                    pyautogui.write(text, interval=0.05)
            elif act == "hotkey":
                keys = step.get("keys", [])
                if keys:
                    pyautogui.hotkey(*keys)
            elif act == "key_press":
                pyautogui.press(step.get("key", "enter"))
            elif act == "click":
                pyautogui.click(int(step.get("x", 0)), int(step.get("y", 0)))
            elif act == "click_text":
                if cfg.get("vision_backend") == "openclaw":
                    run_openclaw(cfg, f"点击屏幕上的 {step.get('text', '')}")
                else:
                    print("     ⚠️ 需要视觉后端：把 config.vision_backend 设为 openclaw 并配置 openclaw_cmd")
            elif act == "move":
                pyautogui.moveTo(int(step.get("x", 0)), int(step.get("y", 0)), duration=0.3)
            elif act == "scroll":
                pyautogui.scroll(int(step.get("amount", 0)))
            elif act == "wait":
                time.sleep(float(step.get("seconds", 1)))
            elif act == "screenshot":
                nm = step.get("name", "shot")
                if step.get("desktop"):
                    d = Path(os.path.expanduser("~/Desktop"))
                else:
                    d = Path(os.path.expanduser(cfg["screenshot_dir"]))
                d.mkdir(parents=True, exist_ok=True)
                path = d / f"{nm}_{int(time.time())}.png"
                try:
                    ok = False
                    # macOS 优先用原生 screencapture（pyautogui 在 mac 上常截空）
                    if sys.platform.startswith("darwin") and shutil.which("screencapture"):
                        r = subprocess.run(["/usr/sbin/screencapture", "-x", str(path)],
                                           capture_output=True, text=True)
                        ok = r.returncode == 0 and Path(str(path)).exists() and Path(str(path)).stat().st_size >= 1000
                    if not ok:
                        img = pyautogui.screenshot(str(path))
                        ok = Path(str(path)).exists() and Path(str(path)).stat().st_size >= 1000
                    if not ok:
                        raise RuntimeError("截图为空，可能缺少「屏幕录制」权限")
                    print(f"     📸 已保存: {path}")
                except Exception as e:
                    print(f"     ⚠️ 截图失败：{e}")
                    print("        macOS 请到「系统设置→隐私与安全性→屏幕录制」给运行 python 的终端授权，并重启终端。")
            elif act == "file_rename":
                folder = os.path.expanduser(step.get("folder", "~/Downloads"))
                pattern = step.get("pattern", "*.png")
                prefix = step.get("prefix", "文件")
                files = sorted(Path(folder).glob(pattern))
                for idx, f in enumerate(files, 1):
                    f.rename(f.with_name(f"{prefix}{idx:03d}{f.suffix}"))
                print(f"     🔁 已重命名 {len(files)} 个文件")
            elif act == "file_move":
                src = os.path.expanduser(step.get("src", ""))
                dst = os.path.expanduser(step.get("dst", ""))
                os.makedirs(os.path.dirname(dst) or dst, exist_ok=True)
                for p in Path().home().glob(src.replace("~/", "")) if src.startswith("~/") else [src]:
                    if os.path.isfile(p):
                        shutil.move(str(p), dst)
                print(f"     📦 已移动 -> {dst}")
            elif act == "clipboard_copy":
                pyautogui.hotkey('command' if sys.platform.startswith('darwin') else 'ctrl', 'c')
            elif act == "clipboard_paste":
                pyautogui.hotkey('command' if sys.platform.startswith('darwin') else 'ctrl', 'v')
            elif act == "window_maximize":
                pyautogui.hotkey('command', 'ctrl', 'f') if sys.platform.startswith('darwin') else pyautogui.hotkey('win', 'up')
            elif act == "window_close":
                pyautogui.hotkey('command' if sys.platform.startswith('darwin') else 'ctrl', 'w')
        except Exception as e:
            print(f"     ⚠️ 该步骤失败: {e}")
        time.sleep(cfg.get("step_delay", 0.8))


# ---------- 模板导出 ----------
def export_templates(path="templates.json"):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(TEMPLATES, f, ensure_ascii=False, indent=2)
    print(f"  📋 已导出模板到 {path}")


# ---------- 主流程 ----------
def print_capabilities():
    print("\n🧠 桌面员工 · 我目前能做的：")
    for c in CAPABILITIES:
        print("   •", c)
    print("\n📦 预设任务模板（直接说编号或整句都行）：")
    for i, t in enumerate(TEMPLATES, 1):
        print(f"   {i}. {t['title']} —— {t['desc']}")
        print(f"      指令示例：{t['instruction']}")
    print()


def main():
    ap = argparse.ArgumentParser(description="本地 AI 桌面代理 MVP v2")
    ap.add_argument("instruction", nargs="?", help="用中文描述你想让电脑做的事（可省略，省略则列出能力与模板）")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--dry-run", action="store_true", help="只生成动作计划，不真执行（安全测试用）")
    ap.add_argument("--templates", action="store_true", help="导出预设任务模板为 templates.json")
    args = ap.parse_args()

    cfg = load_config(args.config)

    if args.templates:
        export_templates()
        return

    if not args.instruction:
        print_capabilities()
        return

    print(f"\n🖥️  桌面员工 · 收到指令：{args.instruction}\n")

    plan = ask_ollama(cfg, args.instruction)
    if not plan:
        print("  ↳ 使用规则兜底计划")
        plan = rule_plan(args.instruction)

    # 系统兜底修正：稳定模型输出的关键动作
    plan = post_process_plan(plan, args.instruction)

    if not plan:
        print("  😅 抱歉，我暂时不能理解这条指令。我目前能做的：")
        for c in CAPABILITIES:
            print("   •", c)
        print("  试试上面的预设模板，或换种说法。\n")
        return

    print(f"  📋 计划共 {len(plan)} 步：")
    for s in plan:
        print("     -", s)

    if args.dry_run:
        print("\n🔍 dry-run 模式：仅生成动作计划，未执行任何桌面操作。\n")
        return

    if cfg.get("safe_mode"):
        # 智能确认：破坏性动作才问，安全动作直接过
        SAFE = {"open_app", "open_url", "type", "hotkey", "key_press",
                "click", "click_text", "move", "scroll", "wait", "screenshot"}
        risky = [s for s in plan if s.get("action") not in SAFE]
        if risky:
            ans = input("\n  ⚠️ 计划含高风险动作(删除/移动/关闭窗口)，确认执行？(y/N) ").strip().lower()
            if ans != "y":
                print("  ⛔ 已取消。")
                return
        else:
            print("  ✅ 均为安全动作，直接执行（不反复确认）。")

    execute(cfg, plan)
    print("\n✅ 完成。\n")


if __name__ == "__main__":
    main()
