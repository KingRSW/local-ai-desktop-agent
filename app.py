#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
桌面员工 · 落地页 + 邮箱收集后端（纯标准库，零依赖）
==================================================
- GET  /            返回 index.html（液态玻璃落地页）
- POST /join        接收 {email} 并追加到 waitlist.csv（真实落库）
- GET  /waitlist    管理员查看已收集邮箱（MVP 用，正式环境请加鉴权）

运行：python app.py  （默认端口 8000，可用 PORT 环境变量覆盖）
部署：作为单端口 HTTP 服务发布即可。
"""

import csv
import os
import json
import re
import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
WAITLIST = os.path.join(ROOT, "waitlist.csv")
PORT = int(os.environ.get("PORT", "8000"))

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def save_email(email: str) -> bool:
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        return False
    write_header = not os.path.exists(WAITLIST)
    with open(WAITLIST, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(["email", "time"])
        w.writerow([email, datetime.datetime.now().isoformat(timespec="seconds")])
    return True


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            try:
                with open(os.path.join(ROOT, "index.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(404, b"index.html not found")
        elif path == "/waitlist":
            if os.path.exists(WAITLIST):
                with open(WAITLIST, "rb") as f:
                    self._send(200, f.read(), "text/csv; charset=utf-8")
            else:
                self._send(200, b"email,time\n", "text/csv; charset=utf-8")
        else:
            self._send(404, b"not found")

    def do_POST(self):
        if urlparse(self.path).path != "/join":
            self._send(404, json.dumps({"ok": False, "msg": "not found"}).encode("utf-8"))
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw.decode("utf-8") or "{}")
            email = str(data.get("email", ""))
        except Exception:
            self._send(400, json.dumps({"ok": False, "msg": "bad request"}).encode("utf-8"))
            return
        ok = save_email(email)
        if ok:
            self._send(200, json.dumps({"ok": True}).encode("utf-8"))
        else:
            self._send(200, json.dumps({"ok": False, "msg": "invalid email"}).encode("utf-8"))

    def log_message(self, *args):
        pass  # 静默日志


if __name__ == "__main__":
    print(f"桌面员工落地页已启动： http://localhost:{PORT}")
    print(f"邮箱将保存到： {WAITLIST}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
