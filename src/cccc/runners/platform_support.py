from __future__ import annotations

import importlib.util
import os
from typing import Any, Dict, List


def pty_support_details() -> Dict[str, Any]:
    """返回当前平台的 PTY 支持诊断信息。"""
    details: Dict[str, Any] = {
        "platform": os.name,
        "supported": True,
        "code": "",
        "message": "",
        "hints": [],
    }

    if os.name != "nt":
        return details

    winpty_spec = importlib.util.find_spec("winpty")
    pywinpty_spec = importlib.util.find_spec("pywinpty")
    if winpty_spec is not None:
        return details

    hints: List[str] = [
        "运行 `python -m pip install pywinpty` 安装 Windows ConPTY 依赖。",
        "如果你使用 uv 安装项目，重新执行 `uv pip install -e .` 也会拉起平台依赖。",
        "安装完成后重新运行 `cccc doctor` 或 `pytest tests/test_windows_pty_backend.py -q` 验证。",
    ]
    if pywinpty_spec is not None:
        message = "已检测到 pywinpty 包，但当前 Python 环境无法导入 `winpty` 模块。"
        code = "winpty_import_failed"
    else:
        message = "当前 Windows 环境缺少 `pywinpty`，PTY actor 无法启动。"
        code = "pywinpty_missing"

    details.update(
        {
            "supported": False,
            "code": code,
            "message": message,
            "hints": hints,
        }
    )
    return details


def pty_support_error_message() -> str:
    """返回适合直接暴露给用户的 PTY 支持错误文案。"""
    details = pty_support_details()
    if bool(details.get("supported")):
        return ""
    hints = details.get("hints") if isinstance(details.get("hints"), list) else []
    suffix = f" {' '.join(str(item) for item in hints if str(item).strip())}".strip()
    message = str(details.get("message") or "当前环境不支持 PTY runner。").strip()
    return f"{message}{(' ' + suffix) if suffix else ''}".strip()
