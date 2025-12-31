"""
CCCC MCP Server — 主入口

支持 stdio 模式运行，供 agent runtimes 调用。

Usage:
    python -m cccc.ports.mcp.main

或通过 CLI:
    cccc mcp
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Optional

from ... import __version__
from .server import MCP_TOOLS, MCPError, handle_tool_call


def _read_message() -> Optional[Dict[str, Any]]:
    """从 stdin 读取一条 JSON-RPC 消息"""
    try:
        line = sys.stdin.readline()
        if not line:
            return None
        return json.loads(line.strip())
    except Exception:
        return None


def _write_message(msg: Dict[str, Any]) -> None:
    """向 stdout 写入一条 JSON-RPC 消息"""
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _make_response(id: Any, result: Any) -> Dict[str, Any]:
    """构造 JSON-RPC 成功响应"""
    return {"jsonrpc": "2.0", "id": id, "result": result}


def _make_error(id: Any, code: int, message: str, data: Any = None) -> Dict[str, Any]:
    """构造 JSON-RPC 错误响应"""
    error: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": id, "error": error}


def handle_request(req: Dict[str, Any]) -> Dict[str, Any]:
    """处理 MCP JSON-RPC 请求"""
    req_id = req.get("id")
    method = str(req.get("method") or "")
    params = req.get("params") or {}

    # MCP 协议方法
    if method == "initialize":
        return _make_response(req_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {},
                # Some MCP clients probe these even if unused; return empty lists below.
                "resources": {},
                "prompts": {},
            },
            "serverInfo": {
                "name": "cccc-mcp",
                "version": __version__,
            },
        })

    if method.startswith("notifications/"):
        # 通知，不需要响应
        return {}

    if method == "tools/list":
        return _make_response(req_id, {"tools": MCP_TOOLS})

    # Optional MCP surfaces (return empty to avoid noisy "Method not found" in some runtimes)
    if method == "resources/list":
        return _make_response(req_id, {"resources": []})

    if method == "prompts/list":
        return _make_response(req_id, {"prompts": []})

    # Common no-op requests some clients send
    if method == "ping":
        return _make_response(req_id, {})

    if method == "logging/setLevel":
        return _make_response(req_id, {})

    if method == "tools/call":
        tool_name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            arguments = {}

        try:
            result = handle_tool_call(tool_name, arguments)
            return _make_response(req_id, {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(result, ensure_ascii=False, indent=2),
                    }
                ],
            })
        except MCPError as e:
            return _make_response(req_id, {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({
                            "error": {
                                "code": e.code,
                                "message": e.message,
                                "details": e.details,
                            }
                        }, ensure_ascii=False, indent=2),
                    }
                ],
                "isError": True,
            })
        except Exception as e:
            return _make_response(req_id, {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({
                            "error": {
                                "code": "internal_error",
                                "message": str(e),
                            }
                        }, ensure_ascii=False, indent=2),
                    }
                ],
                "isError": True,
            })

    # 未知方法
    return _make_error(req_id, -32601, f"Method not found: {method}")


def main() -> int:
    """MCP Server 主循环（stdio 模式）"""
    while True:
        msg = _read_message()
        if msg is None:
            break

        resp = handle_request(msg)
        if resp:  # 通知不需要响应
            _write_message(resp)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
