#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
桌面员工 · 视觉桌面 Agent（混合闭环 v4）
==========================================
针对"弱视觉模型也能用"做的工程化改造：
- 把任务拆成【本地确定性动作】(打开App / 输入中文 / 快捷键 / 搜索) 和
  【视觉定位】(在截图上找某个元素的像素坐标) 两层。
- 弱模型(llava-llama3:8b)只负责"报一个坐标"，不做复杂推理，明显更稳。
- 升级到 qwen2.5-vl 后只需把模型名换掉，定位会更准（自动探测）。

运行前：
  ollama pull llava-llama3:8b        # 当前可用；升级 Ollama 后换 qwen2.5-vl:7b
  pip install pyautogui pyperclip pillow
  授权：系统设置 -> 隐私与安全性 -> 辅助功能(给终端) + 屏幕录制(给终端)
三条执行通道（自动选择，越前面越稳）：
  1) 发消息  -> 微信纯键盘流 + OCR 校验（进会话/发送都核对）
  2) 多步操作 -> 本地确定性执行：本地规则拆步骤，系统 OCR 找元素并点击（不靠大模型）
  3) 其他    -> 通用视觉闭环（兜底，模型越强越好用）
没装 App 的国产应用会**自动改用网页版**（如哔哩哔哩 -> bilibili.com）。

支持的步骤词：打开X / 搜索Y / 点Z / 输入K / 按K / 快捷键 K
  快捷键写法：`按 command+f`、`快捷键 cmd+q`、`按回车`、`按 ⌘⇧A`、`按 esc`

使用：
  python3 desktop_agent.py "打开微信 给文件传输助手 发条消息：在吗"
  python3 desktop_agent.py "打开哔哩哔哩搜索 1123"          # 没装App -> 自动开网页版
  python3 desktop_agent.py "打开计算器 然后点 1"            # 多步：开App + OCR 点按钮
  python3 desktop_agent.py "打开Safari 按 command+t 输入 知乎 按回车"
  python3 desktop_agent.py "打开微信 搜索 深圳天气" --no-exec  # 只看计划不操作
"""

import argparse
import base64
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

_STOP_EVENT = threading.Event()  # 服务端 / CLI 用来中断当前任务

CONFIG = {
    "ollama_host": "http://127.0.0.1:11434",
    "vision_model": None,                 # None=自动选：qwen2.5-vl > llava-llama3:8b > llama3.2-vision:11b
    "max_steps": 18,                      # 最多循环步数
    "step_delay": 1.6,                    # 每步间隔(秒)，给界面反应时间
    "shot_scale_w": 1280,                 # 发给视觉模型的图宽(自动缩放，坐标会还原)
    "shot_dir": "/tmp/desktop_agent_shots",
}

# 视觉模型候选（按能力强弱排序，自动选第一个已安装的）
# 注：新版 Ollama(≥0.34) 已移除 mllama 架构支持，llama3.2-vision 会加载失败，故垫到最后兜底
VISION_CANDIDATES = ["qwen2.5-vl:7b", "qwen2.5-vl:3b", "llava-llama3:8b", "llava:7b", "moondream", "llama3.2-vision:11b"]


def pick_vision_model(host):
    """从已装模型里挑第一个可用的视觉模型（带 ollama list 兜底）。"""
    have = []
    try:
        tags = json.loads(urllib.request.urlopen(host.rstrip("/") + "/api/tags", timeout=8).read())
        have = [m["name"] for m in tags.get("models", [])]
    except Exception:
        # 兜底：直接解析 `ollama list` 输出（不走网络，避免系统代理把 localhost 转发成 502）
        try:
            out = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=10).stdout
            for line in out.splitlines()[1:]:
                cols = line.split()
                if cols and cols[0] not in ("NAME",):
                    have.append(cols[0])
        except Exception:
            pass
    # 优先按候选顺序精确匹配
    for c in VISION_CANDIDATES:
        if c in have:
            return c
    # 模糊匹配：忽略标签，按基础名（如 llama3.2-vision）
    for c in VISION_CANDIDATES:
        base = c.split(":")[0]
        for h in have:
            if h.split(":")[0] == base:
                return h
    return VISION_CANDIDATES[-1]


def _installed_models(host):
    """列出本机已安装的模型名（/api/tags 优先，ollama list 兜底）。"""
    have = []
    try:
        tags = json.loads(urllib.request.urlopen(host.rstrip("/") + "/api/tags", timeout=8).read())
        have = [m["name"] for m in tags.get("models", [])]
    except Exception:
        try:
            out = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=10).stdout
            for line in out.splitlines()[1:]:
                cols = line.split()
                if cols and cols[0] not in ("NAME",):
                    have.append(cols[0])
        except Exception:
            pass
    return have


def _vision_chat(cfg, messages):
    """统一调用视觉模型 /api/chat，返回模型文本；加载失败/报错时按优先级自动降级。
    这样即使某个模型架构不支持（如新版 Ollama 的 llama3.2-vision），也能自动换到可用的。"""
    have = _installed_models(cfg["ollama_host"])
    order = []
    for c in [cfg.get("vision_model")] + VISION_CANDIDATES:
        if not c or c in order:
            continue
        # 未安装的跳过（按基础名模糊判断，避免 qwen2.5-vl:7b vs :3b 误判）
        if have and not any(h.split(":")[0] == c.split(":")[0] for h in have):
            continue
        order.append(c)
    if not order:
        order = [cfg.get("vision_model") or VISION_CANDIDATES[-1]]

    for name in order:
        payload = {"model": name, "messages": messages, "stream": False}
        try:
            req = urllib.request.Request(
                cfg["ollama_host"].rstrip("/") + "/api/chat",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if name != cfg.get("vision_model"):
                print(f"    ℹ️ 视觉模型自动切换为: {name}")
                cfg["vision_model"] = name
            return data["message"]["content"]
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "ignore")
            except Exception:
                pass
            if "unknown model architecture" in body or "failed to load" in body:
                print(f"    ⚠️ {name} 无法加载（架构不支持），自动换下一个视觉模型…")
            else:
                print(f"    ⚠️ {name} 调用失败: HTTP {e.code}")
            continue
        except Exception as e:
            print(f"    ⚠️ {name} 调用失败: {e}")
            continue
    print("    ⚠️ 所有视觉模型均不可用")
    return None


# 中文应用名 -> macOS/Windows 英文名（open_app 用）
APP_MAP = {
    "微信": "WeChat", "计算器": "Calculator", "浏览器": "Safari", "谷歌浏览器": "Google Chrome",
    "设置": "System Settings", "系统设置": "System Settings", "终端": "Terminal",
    "记事本": "TextEdit", "文本编辑": "TextEdit", "日历": "Calendar", "照片": "Photos",
    "音乐": "Music", "邮件": "Mail", "地图": "Maps", "提醒事项": "Reminders",
    "备忘录": "Notes", "预览": "Preview", "访达": "Finder", "文件管理器": "Finder",
    "百度": "Safari", "抖音": "Douyin", "淘宝": "Taobao",
    "哔哩哔哩": "bilibili", "B站": "bilibili", "bilibili": "bilibili",
    "小红书": "RED", "网易云音乐": "NeteaseMusic", "网易云": "NeteaseMusic",
}

# 没装 App 时退回网页版（国产应用大多能用网页版代替）
WEB_MAP = {
    "哔哩哔哩": "https://www.bilibili.com", "B站": "https://www.bilibili.com",
    "bilibili": "https://www.bilibili.com",
    "小红书": "https://www.xiaohongshu.com", "知乎": "https://www.zhihu.com",
    "微博": "https://weibo.com", "淘宝": "https://www.taobao.com",
    "京东": "https://www.jd.com", "抖音": "https://www.douyin.com",
    "百度": "https://www.baidu.com", "网易云音乐": "https://music.163.com",
    "网易云": "https://music.163.com", "豆瓣": "https://www.douban.com",
}

# 快捷键：中文/符号 -> pyautogui 键名
MOD_ALIAS = {
    "command": "command", "cmd": "command", "⌘": "command", "命令": "command", "花键": "command",
    "control": "ctrl", "ctrl": "ctrl", "⌃": "ctrl", "控制": "ctrl", "control键": "ctrl",
    "option": "alt", "alt": "alt", "⌥": "alt", "选项": "alt", "opt": "alt",
    "shift": "shift", "⇧": "shift", "上档": "shift",
}
KEY_ALIAS = {
    "回车": "enter", "确认": "enter", "确定": "enter", "return": "enter", "enter": "enter",
    "空格": "space", "space": "space", "空": "space",
    "退出": "esc", "取消": "esc", "esc": "esc", "escape": "esc",
    "制表": "tab", "tab": "tab", "跳格": "tab",
    "删除": "delete", "delete": "delete", "退格": "backspace", "backspace": "backspace",
    "上": "up", "下": "down", "左": "left", "右": "right",
    "up": "up", "down": "down", "left": "left", "right": "right",
    "home": "home", "end": "end", "pageup": "pageup", "pagedown": "pagedown",
}

# ---------- 视觉定位提示：只让模型报一个坐标（弱模型友好） ----------
SYS_LOCATE = """你正在看一张电脑屏幕截图，宽 {W} 高 {H} 像素。
用户想点击界面上名为「{TARGET}」的元素（可能是按钮、输入框、文字标签或图标）。
请只输出该元素的中心像素坐标，严格用 JSON：
{{"x": 123, "y": 456}}
规则：
- x 必须在 0 到 {W} 之间，y 必须在 0 到 {H} 之间，不要超出边界。
- 如果屏幕上完全看不到「{TARGET}」，输出 {{"found": false}}。
- 只输出 JSON，不要任何解释或 markdown。"""

# ---------- 通用视觉闭环提示（强模型用，弱模型也能跑简单任务） ----------
SYS_PLAN = """你是一个能"看屏幕并操作电脑"的视觉助手。用户给你一张当前电脑屏幕截图，以及要完成的任务。
请只输出【下一步】要执行的【一个】动作，严格用 JSON：
{{"action":"click","nx":0.42,"ny":0.55}}     # 点击(归一化坐标，nx=水平比例 ny=垂直比例，左上原点)
{{"action":"type","text":"要输入的内容"}}      # 输入文字(中文也用这个)
{{"action":"key","key":"enter"}}            # 单键(enter/esc/space/tab/backspace)
{{"action":"hotkey","keys":["command","t"]}} # 组合键
{{"action":"scroll","amount":-3}}           # 滚动(负数向上，正数向下)
{{"action":"open_app","name":"微信"}}        # 打开应用(系统会自动置前)
{{"action":"wait","seconds":2}}             # 等待加载
{{"action":"done"}}                         # 任务已完成

规则：
- 点击坐标用归一化比例(0~1)：nx=水平(0左~1右)、ny=垂直(0上~1底)，点元素中心；必须落在 (0.03,0.97)，不要贴边。
- 若你更习惯像素，也兼容 {{"action":"click","x":123,"y":456}}(基于你看到的截图像素 宽 {W} 高 {H})，但不要返回 (0,0) 或贴边值。
- 屏幕文字可能是中文，请以实际看到的界面为准，点文字/按钮的中心。
- 一次只做一个动作。界面还没出现目标时，先 open_app 或 wait 等待。
- 输入中文直接写在 type 的 text 里（系统用粘贴方式输入，不会乱码）。
- 只有当任务确实完成时才输出 done。只输出 JSON，不要解释。"""

# ---------- 视觉读屏提示：让模型"看"截图并回答一个问题 ----------
SYS_READ = """你正在看一张电脑屏幕截图，宽 {W} 高 {H} 像素。
用户会问一个关于屏幕内容的问题，请仔细观察截图后如实回答，不要编造看不到的内容。
严格只输出 JSON：
{{"see": true, "answer": "用一句话如实回答用户的问题", "text": "你从截图中读到的关键文字原文(可为空)"}}
规则：
- 如果截图里完全看不到用户问的界面或内容，输出 {{"see": false, "answer": "看不到", "text": ""}}。
- 屏幕文字多为中文，请逐字辨认，看不清就说明看不清。
- 只输出 JSON，不要解释或 markdown。"""


# ---------- 截图 ----------
def take_screenshot(cfg):
    """全屏截图 -> 缩放到 shot_scale_w 给模型。返回 (small_path, raw_w, raw_h, scale)。"""
    d = Path(cfg["shot_dir"])
    d.mkdir(parents=True, exist_ok=True)
    raw = d / "shot_raw.png"
    if shutil.which("screencapture"):                       # macOS 原生最稳
        subprocess.run(["/usr/sbin/screencapture", "-x", str(raw)], check=True)
    else:
        import pyautogui
        pyautogui.screenshot(str(raw))
    from PIL import Image
    im = Image.open(raw)
    raw_w, raw_h = im.size
    cfg["_phys_w"], cfg["_phys_h"] = raw_w, raw_h           # 截图是物理像素，Retina 下 > 逻辑分辨率
    scale = cfg["shot_scale_w"] / raw_w                     # 缩放比(小图/原图)
    if scale < 1:
        small = d / "shot_small.png"
        im.resize((cfg["shot_scale_w"], int(raw_h * scale))).save(small)
        return str(small), raw_w, raw_h, scale
    return str(raw), raw_w, raw_h, 1.0


# ---------- 系统 OCR（macOS Vision 框架，中文精度高、离线免费，不依赖大模型） ----------
OCR_SRC = Path(__file__).resolve().parent / "ocr.swift"
OCR_BIN = Path(__file__).resolve().parent / "ocr"


def ensure_ocr():
    """确保 OCR 可执行文件就绪（缺了就用 swiftc 现编一次）。返回路径，失败返回 None。"""
    if OCR_BIN.exists():
        return str(OCR_BIN)
    if not OCR_SRC.exists():
        return None
    print("    🔧 首次使用系统 OCR，正在编译（约十几秒）…")
    try:
        subprocess.run(["swiftc", "-O", str(OCR_SRC), "-o", str(OCR_BIN)],
                       capture_output=True, timeout=300)
    except Exception:
        return None
    return str(OCR_BIN) if OCR_BIN.exists() else None


def ocr_image(path):
    """系统 OCR：返回 [(text, x, y, w, h), ...]，坐标归一化(0~1，左下原点)。失败返回 None。"""
    binp = ensure_ocr()
    if not binp:
        return None
    try:
        out = subprocess.run([binp, str(path)], capture_output=True, text=True, timeout=90).stdout
    except Exception:
        return None
    items = []
    for line in out.splitlines():
        parts = line.split("\t")
        text = parts[0].strip()
        if not text:
            continue
        try:
            x, y, w, h = float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])
        except (IndexError, ValueError):
            x = y = w = h = 0.0
        items.append((text, x, y, w, h))
    return items


def _common_chars(a, b):
    """两串的公共字符数（按重复计数），用于 OCR 错字下的模糊匹配。"""
    return sum((Counter(a) & Counter(b)).values())


def _fuzzy_hit(needle, text):
    """OCR 有错字时的宽松匹配：主串完全包含，或公共字符足够多。"""
    if not needle or not text:
        return False
    if needle in text:
        return True
    need = max(2, len(needle) // 2)
    return _common_chars(needle, text) >= need


def ocr_locate(cfg, target, region=None):
    """用系统 OCR 在最新截图上找文字「target」，返回逻辑屏幕坐标 (x, y, 命中的文字)。
    region: 可选 (x0,y0,x1,y1) 归一化区域（左下原点），用于限定搜索范围。
    找不到返回 None。OCR 对中文远比弱视觉模型准，且离线免费。"""
    raw = str(Path(cfg["shot_dir"]) / "shot_raw.png")
    if not Path(raw).exists():
        return None
    items = ocr_image(raw)
    if not items:
        return None

    def _score(t):
        if not t:
            return 0
        if len(target) <= 2:
            # 极短目标（如"1""确定"）必须几乎完全相等，否则"1"会命中"9月13日""211"这种长文本
            if t.strip() == target:
                return 4
            if target.isascii():
                return 0              # 纯数字/字母短目标：只认完全相等，杜绝误点
            if t.strip().startswith(target):
                return 3              # 短中文词：占位文字前缀命中（如"搜索视频、番剧…"命中"搜索"）
            if target in t and len(t) <= len(target) + 2:
                return 3              # 或只多几个字符（如"Q 搜索"命中"搜索"）
            return 0
        if t == target:
            return 4                      # 完全相等最优先
        if target in t:
            return 3                      # 目标被包含（如"发送"命中"发送(S)"）
        if len(t) >= 2 and t in target:
            return 2                      # 命中是目标的片段
        if _fuzzy_hit(target, t):
            return 1                      # OCR 有错字时的宽松匹配
        return 0

    cands = []
    for it in items:
        t, x, y, w, h = it
        if y + h / 2 > 0.975:             # 顶部菜单栏(含时钟/日期)不是界面元素，排除
            continue
        if y < 0.06:                       # 底部程序坞(Dock)区域，非目标元素，排除
            continue
        if region:
            cx, cy = x + w / 2, y + h / 2
            if not (region[0] <= cx <= region[2] and region[1] <= cy <= region[3]):
                continue
        s = _score(t)
        if s > 0:
            cands.append((s, it))
    if not cands:
        return None

    # 同分取更靠上的那一项（标题/列表项更可能是目标，而不是别的区域里的巧合文字）
    _, (t, x, y, w, h) = max(cands, key=lambda p: (p[0], p[1][2]))
    import pyautogui
    lw, lh = pyautogui.size()             # pyautogui 用逻辑点；OCR 坐标已归一化，直接乘即可
    px = int((x + w / 2) * lw)
    py = int((1 - y - h / 2) * lh)        # OCR 是左下原点，需翻转 y
    if not (0 <= px <= lw and 0 <= py <= lh):
        return None
    return (px, py, t)


# ---------- 点击前 OCR 吸附：修正视觉模型坐标偏差 ----------
def _ocr_snap(cfg, x, y, max_dist=55):
    """点击前，在当前屏幕 OCR 结果里找离 (x,y) 最近、且足够近的文字/按钮中心，
    把点击点吸附过去，修正视觉模型 ±几十像素的固有偏差。无合适目标则原样返回。"""
    import pyautogui
    raw = str(Path(cfg["shot_dir"]) / "shot_raw.png")
    if not Path(raw).exists():
        return x, y
    items = ocr_image(raw)
    if not items:
        return x, y
    lw, lh = pyautogui.size()
    best, best_d = None, max_dist
    for it in items:
        t, tx, ty, tw, th = it
        if ty + th / 2 > 0.975 or ty < 0.06:      # 跳过菜单栏/程序坞
            continue
        cx = int((tx + tw / 2) * lw)
        cy = int((1 - ty - th / 2) * lh)
        d = ((cx - x) ** 2 + (cy - y) ** 2) ** 0.5
        if d <= max_dist and d < best_d:
            best_d, best = d, (cx, cy)
    return best if best else (x, y)


# ---------- 视觉定位：报一个坐标 ----------
def ask_locate(cfg, target, shot_path, w, h):
    """问视觉模型：target 元素在截图里的中心坐标。成功返回 (x,y)，看不到返回 None。"""
    try:
        img_b64 = base64.b64encode(Path(shot_path).read_bytes()).decode("utf-8")
    except Exception as e:
        print(f"    ⚠️ 读截图失败: {e}")
        return None
    messages = [
        {"role": "system", "content": SYS_LOCATE.format(W=w, H=h, TARGET=target)},
        {"role": "user", "content": f"请返回「{target}」的中心坐标。", "images": [img_b64]},
    ]
    content = _vision_chat(cfg, messages)
    if not content:
        return None
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        obj = json.loads(content[start:end + 1])
    except Exception:
        return None
    if obj.get("found") is False:
        return None
    x, y = int(float(obj.get("x", -1))), int(float(obj.get("y", -1)))
    if 0 <= x <= w and 0 <= y <= h and not (x < 5 and y < 5):
        return (x, y)
    return None


# ---------- 视觉决策（通用闭环用） ----------
def ask_plan(cfg, instruction, shot_path, w, h, history):
    """把截图+任务发给视觉模型，返回下一步动作 dict；失败返回 None。"""
    try:
        img_b64 = base64.b64encode(Path(shot_path).read_bytes()).decode("utf-8")
    except Exception as e:
        print(f"    ⚠️ 读截图失败: {e}")
        return None
    hist_txt = "\n".join(history[-8:]) or "（无）"
    prompt = (
        f"任务: {instruction}\n已完成的步骤:\n{hist_txt}\n\n"
        f"当前截图宽 {w} 高 {h}。请输出下一步的【一个】动作 JSON。"
    )
    messages = [
        {"role": "system", "content": SYS_PLAN.format(W=w, H=h)},
        {"role": "user", "content": prompt, "images": [img_b64]},
    ]
    content = _vision_chat(cfg, messages)
    if not content:
        return None
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        obj = json.loads(content[start:end + 1])
    except Exception:
        return None
    # 边缘保护：模型不确定时常返回贴边坐标，丢弃并改为等待重试(避免乱点)
    if obj.get("action") == "click":
        nxx, nyy = obj.get("nx"), obj.get("ny")
        if nxx is not None and nyy is not None:
            if not (0.03 < float(nxx) < 0.97 and 0.03 < float(nyy) < 0.97):
                return {"action": "wait", "seconds": 1.5}
        else:
            cx, cy = obj.get("x"), obj.get("y")
            if cx is not None and cy is not None and not (10 < float(cx) < w - 10 and 10 < float(cy) < h - 10):
                return {"action": "wait", "seconds": 1.5}
    return obj


# ---------- 视觉读屏：截图 + 问题 -> 让模型"看"并回答 ----------
def ask_read(cfg, shot_path, w, h, question):
    """把截图和问题发给视觉模型，返回 dict {see, answer, text}；失败返回 None。"""
    try:
        img_b64 = base64.b64encode(Path(shot_path).read_bytes()).decode("utf-8")
    except Exception as e:
        print(f"    ⚠️ 读截图失败: {e}")
        return None
    messages = [
        {"role": "system", "content": SYS_READ.format(W=w, H=h)},
        {"role": "user", "content": question, "images": [img_b64]},
    ]
    content = _vision_chat(cfg, messages)
    if not content:
        return None
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1:
        return {"see": True, "answer": content.strip(), "text": ""}
    try:
        return json.loads(content[start:end + 1])
    except Exception:
        return {"see": True, "answer": content.strip(), "text": ""}


# ---------- 执行器 ----------
def execute(cfg, act, scale):
    """执行一个动作。scale = 小图宽/原图宽，用于把模型坐标还原到真实屏幕。"""
    import pyautogui
    a = act.get("action")
    if a == "click":
        import pyautogui
        lw, lh = pyautogui.size()
        nx, ny = act.get("nx"), act.get("ny")
        if nx is not None and ny is not None:
            # 归一化坐标(0~1, 左上原点)，分辨率无关，最稳
            fx = max(0.0, min(1.0, float(nx)))
            fy = max(0.0, min(1.0, float(ny)))
            x = int(fx * lw)
            y = int(fy * lh)
        else:
            # 兼容旧式小图像素坐标：还原到截图物理像素再换算成逻辑点
            raw_x = float(act.get("x", 0)) / scale
            raw_y = float(act.get("y", 0)) / scale
            pw = cfg.get("_phys_w") or lw
            ph = cfg.get("_phys_h") or lh
            x = int(raw_x * lw / pw)
            y = int(raw_y * lh / ph)
        # 点击前吸附到真实文字/按钮中心，修正视觉模型偏差
        x, y = _ocr_snap(cfg, x, y)
        pyautogui.moveTo(x, y, duration=0.04)
        pyautogui.click(x, y)
        return f"点击 ({x},{y})"
    if a == "type":
        text = act.get("text", "")
        if any(ord(c) > 127 for c in text):
            import pyperclip
            pyperclip.copy(text)
            pyautogui.hotkey("command" if sys.platform.startswith("darwin") else "ctrl", "v")
        else:
            pyautogui.write(text, interval=0.03)
        return f"输入「{text}」"
    if a == "key":
        k = _parse_key(act.get("key", "enter"))
        k = k[0] if k else "enter"
        pyautogui.press(k)
        return f"按键 {k}"
    if a == "hotkey":
        ks = [k for item in act.get("keys", []) for k in _parse_key(item)]
        if ks:
            pyautogui.hotkey(*ks)
        return f"组合键 {'+'.join(ks)}"
    if a == "scroll":
        pyautogui.scroll(int(act.get("amount", 0)))
        return f"滚动 {act.get('amount')}"
    if a == "open_app":
        name = act.get("name", "")
        tgt = APP_MAP.get(name, name)
        if sys.platform.startswith("win"):
            rc = os.system(f"start {tgt}")
        else:
            # 优先用原始名(如 macOS 微信就叫"微信")，失败再回退映射英文名
            rc = os.system(f"open -a '{name}'")
            if rc != 0:
                rc = os.system(f"open -a '{tgt}'")
            if rc != 0 and name in WEB_MAP:
                # 没装 App -> 打开网页版
                url = WEB_MAP[name]
                print(f"    ℹ️ 没装「{name}」App，已改用网页版 {url}")
                os.system(f"open '{url}'")
                rc = 0
                time.sleep(3.0)
            # 轻量激活到前台（只切前台，不改窗口大小/位置）
            ensure_frontmost(name)
            time.sleep(2.0)
        print(f"    ✅ 打开 {name} (rc={rc})")
        return f"打开 {name}"
    if a == "wait":
        time.sleep(float(act.get("seconds", 1)))
        return f"等待 {act.get('seconds')}s"
    return f"⚠️ 未知动作 {a}"




# ---------- 微信纯键盘流（不依赖视觉，最稳） ----------
def analyze_chat_shot(items, to, msg):
    """从 OCR 结果解析聊天窗口信息：当前会话标题、是否出现目标联系人、聊天区是否出现消息。"""
    wx = [it for it in items if it[1] < 0.5]          # 微信窗口在屏幕左侧
    cands = [it for it in wx if 0.86 < it[2] <= 0.945 and 0.18 < it[1] < 0.36]  # 聊天窗口标题栏区域
    title = min(cands, key=lambda i: i[1])[0] if cands else ""                    # 取最左侧那一行 = 会话名
    chat = [it for it in wx if 0.20 < it[1] < 0.48]   # 聊天区：排除左栏联系人列表与右侧终端
    return {
        "title": title,
        "hit_to": any(_fuzzy_hit(to, it[0]) for it in items) if to else False,
        "title_match": _fuzzy_hit(to, title) if (to and title) else False,
        "hit_msg": any(_fuzzy_hit(msg, it[0]) for it in chat) if msg else False,
        "chat_texts": [it[0] for it in chat],
    }


def wechat_verify_chat(cfg, to, msg, no_exec):
    """发完消息后截图，用 macOS 系统 OCR 读屏：确认当前会话是不是「to」、消息有没有真的出现。
    OCR 比弱视觉模型准得多，且离线免费。"""
    if no_exec:
        print(f"    [不执行] 查看聊天框：确认「{msg}」是否已发到「{to}」")
        return
    try:
        shot, w, h, _ = take_screenshot(cfg)
    except Exception as e:
        print(f"    ⚠️ 查看聊天框失败(截图): {e}")
        return
    items = ocr_image(shot)
    if not items:
        print("    ⚠️ 查看聊天框失败(系统 OCR 不可用)")
        return

    info = analyze_chat_shot(items, to, msg)
    title = info["title"]

    print(f"    👀 OCR 当前会话：{title or '(没识别到标题)'}")
    if to:
        if info["title_match"]:
            print(f"    ✅ OCR 确认：当前就在「{to}」会话")
        elif info["hit_to"]:
            print(f"    ⚠️ OCR 提示：列表里看到「{to}」，但当前会话是「{title}」——可能没进对会话")
        else:
            print(f"    ⚠️ OCR 提示：没看到「{to}」，可能没进对会话")
    if msg:
        if info["hit_msg"]:
            print(f"    ✅ OCR 确认：聊天区出现了「{msg}」")
        else:
            print(f"    ⚠️ OCR 未在聊天区找到「{msg}」")
    if info["chat_texts"]:
        print(f"    📝 聊天区读到：{' | '.join(info['chat_texts'][:6])}")


def wechat_scroll_find(cfg, to, max_scrolls=8):
    """在微信左栏联系人列表里滚动查找「to」，找到就点击。返回是否找到。"""
    import pyautogui
    try:
        lw, lh = pyautogui.size()
    except Exception:
        return False
    cx, cy = int(lw * 0.13), int(lh * 0.45)         # 左栏中部
    pyautogui.moveTo(cx, cy)
    pyautogui.scroll(40)                            # 先滚到列表顶部（正数=向上）
    time.sleep(0.5)
    for i in range(max_scrolls):
        take_screenshot(cfg)
        items = ocr_image(str(Path(cfg["shot_dir"]) / "shot_raw.png")) or []
        cands = [(t, x, y, w, h) for (t, x, y, w, h) in items
                 if x < 0.20 and _fuzzy_hit(to, t)]
        if cands:
            t, x, y, w, h = max(cands, key=lambda v: v[2])
            px = int((x + w / 2) * lw)
            py = int((1 - y - h / 2) * lh)
            print(f"    🖱️ 滚动找到左栏「{t}」({px},{py})")
            pyautogui.click(px, py)
            time.sleep(1.0)
            return True
        pyautogui.scroll(-4)                        # 向下翻一屏的一部分
        time.sleep(0.5)
    return False


def wechat_open_chat(cfg, to, no_exec):
    """进入微信会话：优先用 OCR 在左栏联系人列表里认出「to」并点击（比搜索稳）；
    找不到再用 Cmd+F 搜索兜底。每步都用 OCR 校验当前会话标题，确保真的进去了。"""
    import pyautogui
    pyautogui.PAUSE = 0.12
    if no_exec:
        print(f"    [不执行] 进会话：OCR 找左栏「{to}」→ 点击 → 校验（没有则滚动列表找，再退到 Cmd+F）")
        return False
    ensure_frontmost("微信")   # 轻量激活（只切前台，不改窗口大小），确保操作打进微信
    time.sleep(0.4)

    def _shot_items():
        shot, raw_w, raw_h, scale = take_screenshot(cfg)
        items = ocr_image(str(Path(cfg["shot_dir"]) / "shot_raw.png")) or []
        return items, raw_w, raw_h

    for attempt in range(1, 3):
        items, raw_w, raw_h = _shot_items()
        if not items:
            print("    ⚠️ OCR 不可用，无法校验会话")
            return False
        info = analyze_chat_shot(items, to, "")
        if info["title_match"]:
            print(f"    ✅ 已在「{to}」会话")
            return True

        # 1) 左栏联系人列表里直接找目标并点击（自适应坐标，比键盘搜索稳）
        cands = [(t, x, y, w, h) for (t, x, y, w, h) in items
                 if x < 0.20 and _fuzzy_hit(to, t)]
        if cands:
            t, x, y, w, h = max(cands, key=lambda i: i[2])   # 取最靠上的一项
            lw, lh = pyautogui.size()                        # pyautogui 用逻辑点(Retina 下 ≈ 截图物理像素的一半)
            px = int((x + w / 2) * lw)
            py = int((1 - y - h / 2) * lh)
            if no_exec:
                print(f"    🔍 [不执行] 点击左栏「{t}」于 ({px},{py})")
                return True
            print(f"    🖱️ 点击左栏「{t}」({px},{py})")
            pyautogui.click(px, py)
            time.sleep(1.1)
        else:
            # 2) 左栏没有 → 先滚动联系人列表找（可能是折叠或在下方没显示出来的会话）
            if not wechat_scroll_find(cfg, to):
                # 3) 仍没有 → Cmd+F 搜索兜底
                print(f"    ⌨️ 左栏没看到「{to}」，改用 Cmd+F 搜索")
                pyautogui.keyDown('command'); pyautogui.keyDown('f')
                pyautogui.keyUp('f'); pyautogui.keyUp('command')
                time.sleep(0.7)
                pyautogui.keyDown('command'); pyautogui.keyDown('a')
                pyautogui.keyUp('a'); pyautogui.keyUp('command')
                execute(cfg, {"action": "type", "text": to}, 1.0)
                time.sleep(1.3)
                pyautogui.keyDown('return'); pyautogui.keyUp('return')
                time.sleep(1.3)

        # 校验点击/搜索结果
        items2, _, _ = _shot_items()
        info2 = analyze_chat_shot(items2, to, "")
        if info2["title_match"]:
            print(f"    ✅ 已进入「{to}」会话（OCR 校验通过）")
            return True
        print(f"    ⚠️ 第 {attempt} 次没进去（当前：{info2['title'] or '未知'}），重试…")
        time.sleep(0.5)
    print(f"    ⚠️ 未能确认进入「{to}」会话")
    return False


def wechat_send(cfg, to, msg, no_exec):
    """微信发消息：Cmd+F 搜索 -> OCR 校验进会话 -> 粘贴输入 -> 回车发送 -> OCR 复查。"""
    import pyautogui
    pyautogui.PAUSE = 0.12
    if no_exec:
        print(f"    [不执行] 微信键盘流：搜索「{to}」-> 校验进会话 -> 输入「{msg}」-> 回车发送 -> 复查")
        wechat_verify_chat(cfg, to, msg, no_exec)
        return
    time.sleep(0.8)
    # 进会话：OCR 找联系人点击（带校验与重试）
    wechat_open_chat(cfg, to, no_exec)
    if msg:
        execute(cfg, {"action": "type", "text": msg}, 1.0)
        time.sleep(0.5)
        # 回车发送（微信默认设置）
        pyautogui.keyDown('return'); pyautogui.keyUp('return')
        time.sleep(0.6)
        print(f"    ✅ 微信：给「{to}」发送「{msg}」完成")
        # 发完停一下让消息渲染出来，再用系统 OCR 复查聊天框
        time.sleep(0.8)
        wechat_verify_chat(cfg, to, msg, no_exec)
    else:
        print(f"    ✅ 微信：已进入「{to}」会话（无消息内容）")
        time.sleep(0.8)
        wechat_verify_chat(cfg, to, "（无消息）", no_exec)


# ---------- 轻量激活（仅把应用带到前台，不强行改窗口大小） ----------
def ensure_frontmost(app_name):
    """仅激活到前台，不强制窗口位置/大小，避免打扰用户布局。"""
    if not app_name:
        return
    tgt = APP_MAP.get(app_name, app_name)
    for proc in (tgt, app_name):
        if not proc:
            continue
        try:
            subprocess.run(["osascript", "-e",
                            f'tell application "System Events" to set frontmost of process "{proc}" to true'],
                           capture_output=True, timeout=5)
            time.sleep(0.3)
            return
        except Exception:
            pass


# ---------- 定位并点击（混合闭环核心） ----------
def locate_click(cfg, target, raw_w, raw_h, scale, no_exec):
    """定位并点击：优先用系统 OCR 按屏幕文字精准命中（准/快/离线）；
    OCR 找不到（如图标、纯图形元素）再退回视觉模型。成功返回 True。"""
    import pyautogui
    shot, rw, rh, sc = take_screenshot(cfg)

    # 1) OCR 优先：屏幕上就写着这几个字，直接点它
    loc = ocr_locate(cfg, target)
    if loc:
        x, y, text = loc
        if no_exec:
            print(f"    🔍 [不执行] 将点击「{target}」-> OCR 命中「{text}」({x},{y})")
            return True
        pyautogui.click(x, y)
        print(f"    ✅ 点击「{target}」-> OCR 命中「{text}」({x},{y})")
        return True

    # 2) 兜底：真实执行时不再用弱视觉模型去点图标/无文字元素。
    #    历史证明弱模型（llava）在复杂/全屏画面下给的坐标会点错地方。
    #    若要点图标，请明确走视觉模式或先把目标变成带文字的可见区域。
    if no_exec:
        coord = ask_locate(cfg, target, shot, cfg["shot_scale_w"], int(rh * sc))
        if coord:
            x, y = coord
            print(f"    🔍 [不执行] 将点击「{target}」于 ({int(x / sc)},{int(y / sc)})")
            return True
        print(f"    🔍 OCR 与视觉模型都没找到「{target}」")
        return False
    print(f"    🔍 OCR 没找到「{target}」，停止点击（防止点错）")
    return False


# ---------- 意图解析（本地规则，确定性） ----------
def parse_intent(instruction):
    """把中文指令拆成结构化意图。覆盖：打开App发消息 / 打开App点某文字 / 通用。"""
    instr = instruction.strip()
    intent = {"raw": instr, "app": None, "to": None, "message": None, "click": None}

    # 1) 打开的应用
    m = re.search(r"打开(.+?)(?=给|并|，|,|。|点|搜|发|输入|\s|做|干|$)", instr)
    if m:
        intent["app"] = m.group(1).strip()

    # 2) 收件人：给 XX 发...
    mto = re.search(r"给(.+?)(?:发条|发一条|发消息|发\s*消息|发)", instr)
    if mto:
        intent["to"] = mto.group(1).strip()

    # 3) 消息内容：发(条/一条/消息)[:：] 内容
    mmsg = re.search(r"(?:发条|发一条|发消息|发\s*消息|发)\s*(?:消息)?\s*[:：]?\s*(.+)", instr)
    if mmsg:
        intent["message"] = mmsg.group(1).strip()

    # 4) 点击文字：点 XX / 点击 XX
    mclick = re.search(r"(?:点|点击)(?:一下)?\s*(.+?)(?=并|然后|，|,|。|\s*$)", instr)
    if mclick and not intent["to"]:
        intent["click"] = mclick.group(1).strip()

    return intent


# ---------- 混合闭环：打开App + 发消息 ----------
def run_structured(cfg, intent, no_exec):
    import pyautogui
    pyautogui.FAILSAFE = True
    app = intent["app"] or "微信"        # 只说"给X发消息"时默认微信
    to = intent["to"]
    msg = intent["message"]
    print(f"\n🖥️  混合闭环 · 打开 {app}" + (f" 给 {to} 发消息" if to else "") + "\n")

    def do(act):
        if no_exec:
            print(f"    🔍 [不执行] {act}")
            return
        execute(cfg, act, 1.0)

    # 1) 打开应用（open -a 自带前台行为，不额外强制窗口）
    print(f"  ▶ 打开 {app}")
    do({"action": "open_app", "name": app})
    time.sleep(cfg.get("step_delay", 1.6))

    # 2) 发消息：微信走纯键盘流（最稳，不依赖视觉）；其他 App 走视觉定位
    if to:
        if app == "微信":
            wechat_send(cfg, to, msg, no_exec)
        else:
            print(f"  ▶ 在 {app} 里定位搜索框")
            ok = locate_click(cfg, "搜索", 0, 0, 1.0, no_exec)
            if not ok:
                locate_click(cfg, "搜索框", 0, 0, 1.0, no_exec)
            time.sleep(1.0)
            print(f"  ▶ 输入收件人「{to}」")
            do({"action": "type", "text": to})
            time.sleep(1.2)
            print(f"  ▶ 定位并点击「{to}」会话")
            ok = locate_click(cfg, to, 0, 0, 1.0, no_exec)
            if not ok:
                locate_click(cfg, f"{to}的会话", 0, 0, 1.0, no_exec)
            time.sleep(1.0)
            if msg:
                print(f"  ▶ 定位输入框")
                ok = locate_click(cfg, "输入框", 0, 0, 1.0, no_exec)
                if not ok:
                    locate_click(cfg, "输入消息", 0, 0, 1.0, no_exec)
                time.sleep(0.6)
                print(f"  ▶ 输入消息「{msg}」")
                do({"action": "type", "text": msg})
                time.sleep(0.6)
                print(f"  ▶ 发送")
                ok = locate_click(cfg, "发送", 0, 0, 1.0, no_exec)
                if not ok:
                    do({"action": "key", "key": "enter"})
            else:
                print("    ℹ️ 未检测到消息内容，已打开会话。")
    elif intent.get("click"):
        print(f"  ▶ 定位并点击「{intent['click']}」")
        locate_click(cfg, intent["click"], 0, 0, 1.0, no_exec)

    print("\n✅ 混合闭环执行完毕。\n")


# ---------- 本地确定性步骤解析（复杂任务也不让弱模型做决策） ----------
_MOD_PAT = r"(?:command|cmd|control|ctrl|option|alt|opt|shift|⌘|⌥|⌃|⇧|命令|控制|选项|上档)"
_KEY_PAT = (r"(?:回车|确认|确定|空格|退出|取消|制表|删除|退格|跳格|上|下|左|右|return|enter|space"
            r"|escape|esc|tab|delete|backspace|pageup|pagedown|home|end|up|down|left|right|[A-Za-z0-9])")
_HOTKEY_PAT = rf"(?:{_MOD_PAT}\s*[+＋]?\s*)*(?:{_KEY_PAT})"

STEP_RE = re.compile(
    r"(?P<open>打开(?P<app>[^\s，,。；;]+?)(?=给|并|然后|再|点|搜|发|输入|按|快捷|，|,|。|；|;|\s|$))"
    r"|(?P<press>(?:按下|按一下|按键|按|快捷键|快捷|组合键)\s*(?P<key>" + _HOTKEY_PAT + r"))"
    r"|(?P<search>(?:搜索|搜一下|搜|查找|查询)\s*(?P<query>[^\s，,。；;]+))"
    r"|(?P<click>(?:点击|点)(?:一下)?\s*(?P<target>[^\s，,。；;]+))"
    r"|(?P<type>输入\s*(?P<text>[^\s，,。；;]+))"
)


def _parse_key(token):
    """把 'command+f' / 'cmd + q' / '回车' / '⌘⇧A' 解析成 pyautogui 键名列表。"""
    t = re.sub(r"([⌘⌥⌃⇧])", r" \1 ", token or "")      # 符号修饰键之间自动断开（如 ⌘⇧A）
    parts = [p for p in re.split(r"[+＋\s]+", t.strip()) if p]
    keys = []
    for p in parts:
        low = p.lower()
        if low in MOD_ALIAS:
            keys.append(MOD_ALIAS[low])
        elif p in MOD_ALIAS:
            keys.append(MOD_ALIAS[p])
        elif low in KEY_ALIAS:
            keys.append(KEY_ALIAS[low])
        elif p in KEY_ALIAS:
            keys.append(KEY_ALIAS[p])
        else:
            keys.append(low)
    return keys


def parse_steps(instruction):
    """把中文指令按出现顺序拆成一串【本地确定性动作】。
    能拆出来就直接执行，不再让弱视觉模型做决策，多步任务也能稳。"""
    s = instruction.strip()
    steps = []
    for m in STEP_RE.finditer(s):
        if m.group("open"):
            steps.append({"action": "open_app", "name": m.group("app").strip()})
        elif m.group("press"):
            keys = _parse_key(m.group("key"))
            if not keys:
                continue
            steps.append({"action": "key", "key": keys[0]} if len(keys) == 1
                         else {"action": "hotkey", "keys": keys})
        elif m.group("search"):
            steps.append({"action": "search", "query": m.group("query").strip()})
        elif m.group("click"):
            steps.append({"action": "click", "target": m.group("target").strip()})
        elif m.group("type"):
            steps.append({"action": "type", "text": m.group("text").strip()})
    dedup = []
    for st in steps:                       # 去掉相邻重复步骤
        if not dedup or dedup[-1] != st:
            dedup.append(st)
    return dedup


def run_plan(cfg, steps, no_exec):
    """本地确定性执行器：按步骤逐个执行，每步用系统 OCR 定位，最后回报屏幕内容。
    全程不依赖大模型决策 -> 复杂多步任务也能准确完成。"""
    import pyautogui
    pyautogui.FAILSAFE = True
    n = len(steps)
    print(f"\n🖥️  本地确定性执行 · {n} 步\n")
    for i, st in enumerate(steps, 1):
        if _STOP_EVENT.is_set():
            print("\n⛔ 收到停止指令，终止后续步骤。\n")
            break
        a = st["action"]

        if a == "open_app":
            print(f"  ▶ [{i}/{n}] 打开 {st['name']}")
            if no_exec:
                print(f"    [不执行] 打开 {st['name']}")
            else:
                execute(cfg, {"action": "open_app", "name": st["name"]}, 1.0)
            time.sleep(cfg.get("step_delay", 1.6))

        elif a == "search":
            q = st["query"]
            print(f"  ▶ [{i}/{n}] 搜索「{q}」")
            if no_exec:
                print(f"    [不执行] OCR 找搜索框 -> 输入「{q}」-> 回车")
                continue
            if not locate_click(cfg, "搜索", 0, 0, 1.0, False):
                if not locate_click(cfg, "搜索框", 0, 0, 1.0, False):
                    print("    ⚠️ 没找到搜索框，直接往当前焦点输入")
            time.sleep(0.8)
            execute(cfg, {"action": "type", "text": q}, 1.0)
            time.sleep(0.7)
            pyautogui.press("enter")
            time.sleep(1.5)

        elif a == "click":
            print(f"  ▶ [{i}/{n}] 点击「{st['target']}」")
            if not locate_click(cfg, st["target"], 0, 0, 1.0, no_exec):
                print(f"  ▶ 未找到「{st['target']}」，终止后续步骤（避免误操作）")
                break
            time.sleep(0.9)

        elif a == "type":
            print(f"  ▶ [{i}/{n}] 输入「{st['text']}」")
            if no_exec:
                print(f"    [不执行] 输入「{st['text']}」")
            else:
                execute(cfg, {"action": "type", "text": st["text"]}, 1.0)
            time.sleep(0.7)

        elif a == "key":
            print(f"  ▶ [{i}/{n}] 按键 {st['key']}")
            if no_exec:
                print(f"    [不执行] 按键 {st['key']}")
            else:
                execute(cfg, {"action": "key", "key": st["key"]}, 1.0)
            time.sleep(0.6)

        elif a == "hotkey":
            combo = "+".join(st["keys"])
            print(f"  ▶ [{i}/{n}] 快捷键 {combo}")
            if no_exec:
                print(f"    [不执行] 快捷键 {combo}")
            else:
                execute(cfg, {"action": "hotkey", "keys": st["keys"]}, 1.0)
            time.sleep(0.8)

    # 收尾自查：OCR 看一眼屏幕，把看到的内容回报给用户
    if not no_exec:
        try:
            take_screenshot(cfg)
            items = ocr_image(str(Path(cfg["shot_dir"]) / "shot_raw.png")) or []
            seen = [it[0] for it in items][:14]
            if seen:
                print(f"    👀 屏幕当前文字：{' | '.join(seen)}")
        except Exception:
            pass
    print("\n✅ 步骤执行完毕。\n")


# ---------- 通用视觉闭环（兜底；强模型才好用） ----------
def run_vision(cfg, instruction, no_exec):
    import pyautogui
    pyautogui.FAILSAFE = True
    print(f"\n🖥️  视觉桌面 Agent · 任务：{instruction}\n")
    history = []
    for step in range(1, cfg["max_steps"] + 1):
        if _STOP_EVENT.is_set():
            print("\n⛔ 收到停止指令，终止视觉闭环。\n")
            return
        shot, raw_w, raw_h, scale = take_screenshot(cfg)
        print(f"  🔄 第{step}步 · 截图({raw_w}x{raw_h}) -> 问视觉模型…")
        act = ask_plan(cfg, instruction, shot, cfg["shot_scale_w"], int(raw_h * scale), history)
        if not act:
            print("    ↳ 模型无决策，重试一次…")
            act = ask_plan(cfg, instruction, shot, cfg["shot_scale_w"], int(raw_h * scale), history)
            if not act:
                print("    ⛔ 连续失败，终止。")
                return
        print(f"    🤖 决策: {act}")
        if act.get("action") == "done":
            print("\n✅ 视觉 Agent 判定任务完成。\n")
            return
        if no_exec:
            history.append(f"第{step}步(未执行): {act}")
            print("    🔍 --no-exec 模式：仅记录决策，不操作桌面。")
            continue
        result = execute(cfg, act, scale)
        history.append(f"第{step}步: {act} -> {result}")
        print(f"    ✅ {result}")
        time.sleep(cfg.get("step_delay", 1.6))
    print("\n⏱️  已达最大步数。若任务未完成，可重新下达更具体的指令。\n")


def main():
    ap = argparse.ArgumentParser(description="视觉桌面 Agent（混合闭环 v4）")
    ap.add_argument("instruction", help="用中文描述你想让电脑做的事")
    ap.add_argument("--no-exec", action="store_true", help="只看决策，不真操作桌面（安全调试用）")
    ap.add_argument("--vision-model", default=None, help="覆盖视觉模型，如 qwen2.5-vl:7b")
    ap.add_argument("--max-steps", type=int, default=18, help="最大循环步数（通用闭环用）")
    args = ap.parse_args()

    cfg = dict(CONFIG)
    cfg["max_steps"] = args.max_steps
    if args.vision_model:
        cfg["vision_model"] = args.vision_model
    if not cfg.get("vision_model"):
        cfg["vision_model"] = pick_vision_model(cfg["ollama_host"])
        print(f"  🧠 自动选择视觉模型: {cfg['vision_model']}")

    intent = parse_intent(args.instruction)
    steps = parse_steps(args.instruction)
    print(f"  🧩 解析出的步骤: {steps}")
    # 路由：发消息 -> 混合闭环；其余能解析的 -> 本地确定性执行；都解析不出 -> 通用视觉闭环兜底
    if intent["to"] or intent["message"]:
        run_structured(cfg, intent, no_exec=args.no_exec)
    elif steps:
        run_plan(cfg, steps, no_exec=args.no_exec)
    else:
        run_vision(cfg, args.instruction, no_exec=args.no_exec)


if __name__ == "__main__":
    main()
