#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
桌面员工 · 图形界面（pywebview + 液态玻璃 UI）
================================================
- 复用 agent/desktop_agent.py 的三条执行通道（微信键盘流 / 本地拆步 / 视觉闭环）
- 后台线程跑任务，stdout 实时转发到界面日志
- 侧栏：环境自检（模型服务 / OCR / 辅助功能 / 屏幕录制权限）、模型选择、步进间隔
- 支持「仅预览」(no-exec)、随时停止、执行后回显屏幕截图

源码运行： python3 gui/app_gui.py
打包运行： python3 setup_app.py py2app  ->  dist/桌面员工.app
"""

import base64
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from io import BytesIO
from pathlib import Path

APP_NAME = "桌面员工"
HERE = Path(__file__).resolve().parent
IS_FROZEN = bool(getattr(sys, "frozen", False))

# Finder 启动的 .app PATH 很窄，补上 ollama / brew 等常见位置
_EXTRA_PATH = [
    "/usr/local/bin", "/opt/homebrew/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin",
    str(Path.home() / ".local" / "bin"),
    "/Applications/Ollama.app/Contents/Resources",
]
os.environ["PATH"] = os.pathsep.join(
    [p for p in os.environ.get("PATH", "").split(os.pathsep) if p] +
    [p for p in _EXTRA_PATH if p not in os.environ.get("PATH", "").split(os.pathsep)]
)


# ---------------------------------------------------------------- 路径定位
def _app_resources():
    """打包后 .app/Contents/Resources"""
    p = Path(sys.executable).resolve().parent
    for up in (p, p.parent):
        if (up / "Resources").exists():
            return up / "Resources"
    return p


def _find_agent_dir():
    cands = [HERE.parent / "agent", HERE / "agent", HERE]
    if IS_FROZEN:
        res = _app_resources()
        cands = [res / "agent", res] + cands
    for c in cands:
        if (c / "desktop_agent.py").exists():
            return c
    raise RuntimeError("找不到 agent/desktop_agent.py")


def _find_ui():
    cands = [HERE / "ui.html"]
    if IS_FROZEN:
        res = _app_resources()
        cands = [res / "ui.html", res / "gui" / "ui.html"] + cands
    for c in cands:
        if c.exists():
            return str(c)
    raise RuntimeError("找不到 ui.html")


AGENT_DIR = _find_agent_dir()
UI_HTML = _find_ui()


# ---------------------------------------------------------------- 加载核心
def _load_core():
    spec = importlib.util.spec_from_file_location(
        "desktop_agent_core", AGENT_DIR / "desktop_agent.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["desktop_agent_core"] = mod
    spec.loader.exec_module(mod)
    return mod


core = _load_core()

# 设置持久化位置
CFG_DIR = Path.home() / "Library" / "Application Support" / APP_NAME
CFG_FILE = CFG_DIR / "settings.json"


def _load_settings():
    try:
        return json.loads(CFG_FILE.read_text("utf-8"))
    except Exception:
        return {}


def _save_settings(data):
    try:
        CFG_DIR.mkdir(parents=True, exist_ok=True)
        CFG_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------- 日志管道
_QLOCK = threading.Lock()
_LOGS = []
_SINK_BUF = ""
_STATE = {"running": False, "ok": None, "text": "就绪", "shot": None}
_STOP = threading.Event()
_VERBOSE = {"on": False}
_AUTOSHOT = {"on": True}


def _emit(line):
    s = (line or "").strip()
    if not s:
        return
    if not _VERBOSE["on"] and "👀 屏幕当前文字" in s:
        return
    with _QLOCK:
        _LOGS.append(s)
        if len(_LOGS) > 800:
            del _LOGS[:200]


class _StdoutSink:
    """把 core 里的 print 按行喂给界面日志。"""
    encoding = "utf-8"

    def write(self, s):
        global _SINK_BUF
        if not s:
            return
        _SINK_BUF += s
        while "\n" in _SINK_BUF:
            ln, _SINK_BUF = _SINK_BUF.split("\n", 1)
            _emit(ln)

    def flush(self):
        pass

    def isatty(self):
        return False


# 让「停止」按钮能在下一步立即生效
_ORIG_EXECUTE = core.execute


def _patched_execute(cfg, act, scale):
    if _STOP.is_set():
        raise KeyboardInterrupt("用户停止")
    return _ORIG_EXECUTE(cfg, act, scale)


core.execute = _patched_execute


# ---------------------------------------------------------------- 执行线程
def _snapshot_b64():
    """执行后截一张屏，压缩成 JPEG data-url 回显到界面。"""
    try:
        core.take_screenshot(core.CONFIG)
        raw = Path(core.CONFIG["shot_dir"]) / "shot_raw.png"
        if not raw.exists():
            return None
        from PIL import Image
        im = Image.open(raw).convert("RGB")
        im.thumbnail((1280, 1280))
        buf = BytesIO()
        im.save(buf, "JPEG", quality=78, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def _worker(cmd, preview, model, delay):
    _STATE.update(running=True, ok=None, text="执行中")
    old_stdout = sys.stdout
    sys.stdout = _StdoutSink()
    try:
        cfg = core.CONFIG
        cfg["step_delay"] = max(0.3, float(delay or 1.6))
        if model:
            cfg["vision_model"] = model
            _emit(f"🧠 使用视觉模型: {model}")
        elif not cfg.get("vision_model"):
            cfg["vision_model"] = core.pick_vision_model(cfg["ollama_host"])
            _emit(f"🧠 自动选择视觉模型: {cfg['vision_model']}")

        intent = core.parse_intent(cmd)
        steps = core.parse_steps(cmd)
        if steps:
            _emit("🧩 解析出的步骤: " + json.dumps(steps, ensure_ascii=False))
        if preview:
            _emit("🔍 仅预览模式：只做决策与识别，不操作桌面")

        if intent.get("to") or intent.get("message"):
            core.run_structured(cfg, intent, no_exec=preview)
        elif steps:
            core.run_plan(cfg, steps, no_exec=preview)
        else:
            core.run_vision(cfg, cmd, no_exec=preview)

        _STATE.update(ok=True, text="已完成")
    except KeyboardInterrupt:
        _emit("⛔ 已停止")
        _STATE.update(ok=False, text="已停止")
    except Exception as e:
        _emit(f"⛔ 执行出错: {e}")
        _STATE.update(ok=False, text="执行出错")
    finally:
        sys.stdout = old_stdout
        if _AUTOSHOT["on"] and not preview:
            shot = _snapshot_b64()
            if shot:
                _STATE["shot"] = shot
        _STATE["running"] = False


# ---------------------------------------------------------------- JS API
class Api:
    window = None          # 由 main() 注入
    _maxed = False

    def win(self, action):
        w = self.window
        if not w:
            return {"ok": False}
        try:
            if action == "close":
                w.destroy()
            elif action == "min":
                w.minimize()
            elif action == "max":
                if Api._maxed:
                    w.restore()
                else:
                    w.maximize()
                Api._maxed = not Api._maxed
        except Exception:
            return {"ok": False}
        return {"ok": True}

    def poll(self):
        with _QLOCK:
            lines = list(_LOGS)
            _LOGS.clear()
        shot, _STATE["shot"] = _STATE.get("shot"), None
        return {
            "lines": lines,
            "running": _STATE["running"],
            "ok": _STATE["ok"],
            "state": _STATE["text"] if not _STATE["running"] else None,
            "shot": shot,
        }

    def run(self, cmd, preview=False, model="", delay=1.6):
        if _STATE["running"]:
            return {"ok": False, "msg": "任务执行中"}
        cmd = (cmd or "").strip()
        if not cmd:
            return {"ok": False, "msg": "指令为空"}
        _STOP.clear()
        t = threading.Thread(target=_worker, args=(cmd, bool(preview), model or "", delay),
                             daemon=True)
        t.start()
        return {"ok": True}

    def stop(self):
        _STOP.set()
        core.CONFIG["_stop"] = True
        _emit("⏹ 已请求停止，当前动作结束后中断…")
        return {"ok": True}

    def env_check(self):
        host = core.CONFIG["ollama_host"].rstrip("/")
        models, ollama = [], False
        try:
            tags = json.loads(urllib.request.urlopen(host + "/api/tags", timeout=2.5).read())
            models = [m["name"] for m in tags.get("models", [])]
            ollama = True
        except Exception:
            try:
                out = subprocess.run(["ollama", "list"], capture_output=True, text=True,
                                     timeout=6).stdout
                for line in out.splitlines()[1:]:
                    c = line.split()
                    if c and c[0] != "NAME":
                        models.append(c[0])
                ollama = bool(models)
            except Exception:
                pass

        ocr = bool(core.ensure_ocr())

        ax = sc = None
        try:
            import Quartz
            try:
                ax = bool(Quartz.AXIsProcessTrusted())
            except Exception:
                ax = None
            try:
                sc = bool(Quartz.CGPreflightScreenCaptureAccess())
            except Exception:
                sc = None
        except Exception:
            pass

        vis = [m for m in models if any(k in m for k in
               ("vl", "llava", "vision", "moondream", "minicpm-v", "gemma3"))]
        return {"ollama": ollama, "models": models, "vision_models": vis,
                "model": core.CONFIG.get("vision_model") or "",
                "ocr": ocr, "ax": ax, "sc": sc}

    def open_settings(self, which):
        urls = {
            "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
            "screencapture": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
        }
        url = urls.get(which)
        if url:
            subprocess.run(["open", url], check=False)
        return {"ok": True}

    def set_verbose(self, on):
        _VERBOSE["on"] = bool(on)
        _save_settings({"verbose": bool(on), "autoshot": _AUTOSHOT["on"]})
        return {"ok": True}

    def set_autoshot(self, on):
        _AUTOSHOT["on"] = bool(on)
        _save_settings({"verbose": _VERBOSE["on"], "autoshot": bool(on)})
        return {"ok": True}

    def save_prefs(self, model="", delay=1.6):
        _save_settings({"verbose": _VERBOSE["on"], "autoshot": _AUTOSHOT["on"],
                        "model": model, "delay": delay})
        return {"ok": True}


# ---------------------------------------------------------------- 启动
def _make_activate(window):
    """把窗口带到最前（双击 .app 打开时是标准行为）。"""
    def _do():
        try:
            from AppKit import NSApplication, NSRunningApplication
            NSRunningApplication.currentApplication().activateWithOptions_(1 << 1)
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        except Exception:
            pass
        if os.environ.get("AGENT_TOPMOST"):
            try:
                window.on_top = True
            except Exception:
                pass

    def _schedule():
        try:
            from PyObjCTools import AppHelper
            AppHelper.callAfter(_do)
        except Exception:
            _do()
    return _schedule


def main():
    import webview

    st = _load_settings()
    _VERBOSE["on"] = bool(st.get("verbose", False))
    _AUTOSHOT["on"] = bool(st.get("autoshot", True))
    if st.get("model"):
        core.CONFIG["vision_model"] = st["model"]

    kwargs = dict(title=APP_NAME, url=UI_HTML, js_api=Api(),
                  width=1020, height=768, min_size=(900, 640),
                  frameless=False, easy_drag=False, shadow=True,
                  background_color="#0a0b10")
    try:
        window = webview.create_window(**kwargs)
    except TypeError:
        for k in ("min_size", "shadow", "easy_drag", "frameless"):
            kwargs.pop(k, None)
        window = webview.create_window(**kwargs)
    Api.window = window

    try:
        webview.start(debug=bool(os.environ.get("AGENT_DEBUG")), gui="cocoa",
                      func=_make_activate(window))
    except TypeError:
        webview.start(gui="cocoa")


if __name__ == "__main__":
    main()
