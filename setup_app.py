#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
桌面员工 · py2app 打包配置
============================
用法：
    python3 setup_app.py py2app            # 生成 dist/DesktopEmployee.app
    bash build_dmg.sh                      # 直接出 .dmg（推荐）

说明：
- 主程序 = gui/app_gui.py（pywebview 窗口）
- agent/ 目录整包放进 Resources/agent/，core 用 importlib 动态加载
- 图标 gui/icon.icns 由 build/gen_icon.py 生成
"""

import os
from pathlib import Path

# ---------------------------------------------------------------- 兼容补丁
# 本机 Python 3.13 把 zlib 静态编译进了解释器（属于内置模块，没有 __file__），
# 而 py2app 打包时会无条件读取 zlib.__file__ 往 app 里复制一份 → AttributeError。
# 这里补一个存在的路径，让它复制过去（用系统 libz，无害；缺了就用本文件兜底）。
import zlib  # noqa: E402

if not hasattr(zlib, "__file__"):
    _fallback = "/usr/lib/libz.1.dylib"
    zlib.__file__ = _fallback if os.path.exists(_fallback) else __file__

from setuptools import setup  # noqa: E402

ROOT = Path(__file__).resolve().parent
ICON = ROOT / "gui" / "icon.icns"

DATA_FILES = [
    ("agent", [
        "agent/desktop_agent.py",
        "agent/ocr",
        "agent/ocr.swift",
        "agent/config.json",
        "agent/config.example.json",
        "agent/templates.json",
    ]),
    ("", ["gui/ui.html"]),
]

PACKAGES = [
    "webview",
    "bottle",
    "objc",
    "AppKit",
    "Foundation",
    "Quartz",
    # 注意：PyObjCTools 是无 __init__.py 的 namespace package，
    # 列进 packages 会让 py2app 的目录查找失败，交给 modulegraph 自动收集即可
    "pyautogui",
    "pyperclip",
    "PIL",
    "pyscreeze",
    "pytweening",
    "pymsgbox",
    "mouseinfo",
]

PLIST = {
    "CFBundleName": "桌面员工",
    "CFBundleDisplayName": "桌面员工",
    "CFBundleExecutable": "DesktopEmployee",
    "CFBundleIdentifier": "com.kingrsw.desktopployee",
    "CFBundleShortVersionString": "1.0.0",
    "CFBundleVersion": "1.0.0",
    "LSMinimumSystemVersion": "12.0",
    "NSHighResolutionCapable": True,
    "NSRequiresAquaSystemAppearance": False,
    "NSAppleEventsUsageDescription": "桌面员工需要控制其他应用，才能替你完成任务。",
    "NSHumanReadableCopyright": "本地离线运行 · 数据不出本机",
}

OPTIONS = {
    "argv_emulation": False,
    "packages": PACKAGES,
    # pymsgbox 会顺手 import tkinter，但本机 Tcl 不完整会让 py2app 的 recipe 崩掉；
    # 界面已经全在 WebView 里，用不到 tk，直接排除。
    "excludes": ["tkinter", "_tkinter", "Tkinter", "PIL.ImageTk",
                 "test", "unittest", "pydoc_data"],
    "plist": PLIST,
    "frameworks": [],
    "resources": [],
    "site_packages": True,
    "strip": False,
    "optimize": 0,
}

if ICON.exists():
    OPTIONS["iconfile"] = str(ICON)

setup(
    name="DesktopEmployee",
    version="1.0.0",
    description="桌面员工 · 本地 AI 电脑操作助手",
    app=["gui/app_gui.py"],
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
