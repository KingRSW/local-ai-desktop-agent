#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
桌面员工 · Mac 端遥控服务
============================================================
手机 / 浏览器连同一 WiFi -> 发中文指令 -> Mac 真机执行 -> 实时截图 + 日志回传。

用法:
    python3 agent/server.py                 # 默认 0.0.0.0:8742
    python3 agent/server.py --port 8742 --token abc123   # 加连接令牌
    python3 agent/server.py --host 127.0.0.1             # 仅本机(更私密)

手机端:
    浏览器打开  http://<你的Mac局域网IP>:8742
    （Mac IP: 系统设置 -> 网络，或终端 `ipconfig getifaddr en0`）
"""
import sys
import os
import io
import json
import time
import threading
import argparse
import contextlib
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import desktop_agent as core  # 复用本地引擎

PORT = 8742
HOST = "0.0.0.0"
ACCESS_TOKEN = os.environ.get("DESKTOP_AGENT_TOKEN", "")
SHOT_DIR = Path(core.CONFIG["shot_dir"])

# ----------------------------------------------------------- 任务状态
class _Writer(io.TextIOBase):
    """把 desktop_agent 的 print 实时收集进任务日志。"""
    def __init__(self, sink):
        self.sink = sink
    def write(self, s):
        if s:
            self.sink(s)
        return len(s)

_task = {
    "running": False,
    "started_at": 0.0,
    "finished_at": 0.0,
    "cmd": "",
    "log": [],        # [{"t": "HH:MM:SS", "msg": "..."}]
    "error": "",
}
_lock = threading.Lock()


def _log(msg):
    line = (msg or "").rstrip("\n")
    if not line:
        return
    ts = time.strftime("%H:%M:%S")
    with _lock:
        _task["log"].append({"t": ts, "msg": line})
        if len(_task["log"]) > 600:
            _task["log"] = _task["log"][-600:]


def _snap_daemon(stop_ev):
    """任务执行期间持续截图，让手机端看到实时画面。"""
    while not stop_ev.is_set():
        try:
            core.take_screenshot(core.CONFIG)
        except Exception:
            pass
        stop_ev.wait(2.5)


def _run_worker(instruction, vision_model, no_exec):
    core._STOP_EVENT.clear()
    stop_ev = threading.Event()
    threading.Thread(target=_snap_daemon, args=(stop_ev,), daemon=True).start()
    cfg = dict(core.CONFIG)
    cfg["max_steps"] = 18
    if vision_model:
        cfg["vision_model"] = vision_model
    if not cfg.get("vision_model"):
        try:
            cfg["vision_model"] = core.pick_vision_model(cfg["ollama_host"])
            _log(f"🧠 自动选择视觉模型: {cfg['vision_model']}")
        except Exception as e:
            _log(f"⚠️ 选模型失败: {e}")
    try:
        with contextlib.redirect_stdout(_Writer(_log)):
            intent = core.parse_intent(instruction)
            steps = core.parse_steps(instruction)
            _log(f"🧩 解析步骤: {steps}")
            if intent["to"] or intent["message"]:
                core.run_structured(cfg, intent, no_exec=no_exec)
            elif steps:
                core.run_plan(cfg, steps, no_exec=no_exec)
            else:
                core.run_vision(cfg, instruction, no_exec=no_exec)
    except Exception as e:
        import traceback
        _log(f"⛔ 执行出错: {e}")
        _log(traceback.format_exc())
    finally:
        stop_ev.set()
        with _lock:
            _task["running"] = False
            _task["finished_at"] = time.time()


def start_task(instruction, vision_model=None, no_exec=False):
    with _lock:
        if _task["running"]:
            return False, "已有任务在执行，请稍候"
        _task["running"] = True
        _task["started_at"] = time.time()
        _task["finished_at"] = 0.0
        _task["cmd"] = instruction
        _task["log"] = []
        _task["error"] = ""
    threading.Thread(target=_run_worker,
                     args=(instruction, vision_model, no_exec),
                     daemon=True).start()
    return True, "已启动"


def manual_snap():
    try:
        core.take_screenshot(core.CONFIG)
        return True
    except Exception as e:
        _log(f"截图失败: {e}")
        return False


# ----------------------------------------------------------- HTTP
CONSOLE_HTML = (HERE / "console.html").read_text(encoding="utf-8")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # 静默

    def _send(self, code, body, ctype=None, extra=None):
        if isinstance(body, (dict, list)):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            ctype = ctype or "application/json; charset=utf-8"
        elif isinstance(body, str):
            data = body.encode("utf-8")
            ctype = ctype or "text/plain; charset=utf-8"
        else:
            data = body
            ctype = ctype or "application/octet-stream"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        p = u.path
        if p in ("/", "/index.html"):
            self._send(200, CONSOLE_HTML, "text/html; charset=utf-8")
        elif p == "/api/health":
            self._send(200, {"ok": True, "model": core.CONFIG.get("vision_model"),
                             "running": _task["running"], "token_required": bool(ACCESS_TOKEN)})
        elif p == "/api/status":
            with _lock:
                snap = dict(_task)
            snap["log"] = snap["log"][-220:]
            self._send(200, snap)
        elif p == "/api/shot":
            raw = SHOT_DIR / "shot_raw.png"
            if raw.exists():
                self._send(200, raw.read_bytes(), "image/png")
            else:
                self._send(404, {"error": "no shot yet"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            data = {}
        if u.path == "/api/run":
            cmd = (data.get("cmd") or "").strip()
            token = data.get("token", "")
            vision = data.get("model")
            no_exec = bool(data.get("no_exec", False))
            if ACCESS_TOKEN and token != ACCESS_TOKEN:
                self._send(403, {"error": "连接令牌(token)错误"})
                return
            if not cmd:
                self._send(400, {"error": "cmd 为空"})
                return
            ok, msg = start_task(cmd, vision, no_exec)
            if not ok:
                self._send(409, {"error": msg})
                return
            self._send(200, {"ok": True, "msg": msg})
        elif u.path == "/api/stop":
            if ACCESS_TOKEN and data.get("token", "") != ACCESS_TOKEN:
                self._send(403, {"error": "连接令牌(token)错误"})
                return
            core._STOP_EVENT.set()
            was = False
            with _lock:
                was = _task["running"]
                _task["running"] = False
                _task["finished_at"] = time.time()
            if was:
                _log("🛑 收到停止指令，当前任务已终止")
            self._send(200, {"ok": True, "was_running": was})
        elif u.path == "/api/snap":
            if ACCESS_TOKEN and data.get("token", "") != ACCESS_TOKEN:
                self._send(403, {"error": "连接令牌(token)错误"})
                return
            ok = manual_snap()
            self._send(200, {"ok": ok})
        else:
            self._send(404, {"error": "not found"})


def main():
    global PORT, HOST, ACCESS_TOKEN
    ap = argparse.ArgumentParser(description="桌面员工 · Mac 端遥控服务")
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--token", default=None, help="连接令牌(手机端需填，防止他人乱控电脑)")
    args = ap.parse_args()
    HOST, PORT = args.host, args.port
    if args.token:
        ACCESS_TOKEN = args.token
    # 默认不加令牌：本地/局域网自用，手机端直接连。需要防护时再 --token xxx。

    try:
        core.ensure_ocr()
        _log("✅ OCR 就绪")
    except Exception as e:
        _log(f"⚠️ OCR 准备失败(不影响基础功能): {e}")

    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print("=" * 52)
    print("🖥️  桌面员工 · 遥控服务已启动")
    print(f"   本机:      http://127.0.0.1:{PORT}")
    print(f"   手机(同WiFi): http://<你的Mac局域网IP>:{PORT}")
    print(f"   连接令牌:  {ACCESS_TOKEN or '(无)'}")
    print("   手机端首次打开后，把上面『连接令牌』填进设置即可。")
    print("=" * 52)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
