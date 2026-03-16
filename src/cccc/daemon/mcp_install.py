"""Runtime MCP installation helpers."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Dict

from ..kernel.runtime import get_cccc_mcp_stdio_command
from ..util.conv import coerce_bool
from ..util.fs import read_json


def _parse_codex_mcp_get_output(output: str) -> Dict[str, str]:
    parsed: Dict[str, str] = {}
    for raw in str(output or "").splitlines():
        line = raw.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        parsed[key.strip().lower()] = value.strip()
    return parsed


def _normalize_mcp_command_value(value: str) -> str:
    normalized = str(value or "").strip().strip('"').strip("'")
    if sys.platform.startswith("win"):
        return normalized.replace("/", "\\").lower()
    return normalized


def _codex_mcp_entry_matches_expected(output: str, expected_cmd: list[str]) -> bool:
    entry = _parse_codex_mcp_get_output(output)
    if not entry:
        return False
    if str(entry.get("enabled", "true")).strip().lower() == "false":
        return False
    if str(entry.get("transport", "stdio")).strip().lower() != "stdio":
        return False
    if not sys.platform.startswith("win"):
        return True
    if not expected_cmd:
        return False
    command = _normalize_mcp_command_value(entry.get("command", ""))
    expected_command = _normalize_mcp_command_value(expected_cmd[0])
    if not command or command != expected_command:
        return False
    args = str(entry.get("args", "")).strip()
    expected_args = " ".join(str(part or "").strip() for part in expected_cmd[1:] if str(part or "").strip())
    return args == expected_args


def is_mcp_installed(runtime: str) -> bool:
    try:
        if runtime == "claude":
            result = subprocess.run(
                ["claude", "mcp", "get", "cccc"],
                capture_output=True,
                timeout=10,
            )
            return result.returncode == 0

        if runtime == "codex":
            result = subprocess.run(
                ["codex", "mcp", "get", "cccc"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return False
            return _codex_mcp_entry_matches_expected(result.stdout, get_cccc_mcp_stdio_command())

        if runtime == "droid":
            for cfg_path in (
                Path.home() / ".factory" / "mcp.json",
                Path.home() / ".config" / "droid" / "mcp.json",
                Path.home() / ".droid" / "mcp.json",
            ):
                cfg = read_json(cfg_path)
                servers = cfg.get("mcpServers") if isinstance(cfg, dict) else None
                if not isinstance(servers, dict):
                    continue
                entry = servers.get("cccc")
                if isinstance(entry, dict):
                    return not coerce_bool(entry.get("disabled"), default=False)
            return False

        if runtime == "amp":
            settings_path = Path.home() / ".config" / "amp" / "settings.json"
            if not settings_path.exists():
                return False
            doc = json.loads(settings_path.read_text(encoding="utf-8") or "{}")
            if not isinstance(doc, dict):
                return False
            servers = doc.get("amp.mcpServers")
            return isinstance(servers, dict) and "cccc" in servers

        if runtime == "auggie":
            settings_path = Path.home() / ".augment" / "settings.json"
            if not settings_path.exists():
                return False
            doc = json.loads(settings_path.read_text(encoding="utf-8") or "{}")
            if not isinstance(doc, dict):
                return False
            servers = doc.get("mcpServers")
            return isinstance(servers, dict) and "cccc" in servers

        if runtime == "neovate":
            config_path = Path.home() / ".neovate" / "config.json"
            if not config_path.exists():
                return False
            doc = json.loads(config_path.read_text(encoding="utf-8") or "{}")
            if not isinstance(doc, dict):
                return False
            servers = doc.get("mcpServers")
            return isinstance(servers, dict) and "cccc" in servers

        if runtime == "gemini":
            settings_path = Path.home() / ".gemini" / "settings.json"
            if not settings_path.exists():
                return False
            doc = json.loads(settings_path.read_text(encoding="utf-8") or "{}")
            if not isinstance(doc, dict):
                return False
            servers = doc.get("mcpServers")
            return isinstance(servers, dict) and "cccc" in servers

        if runtime == "kimi":
            result = subprocess.run(
                ["kimi", "mcp", "list"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return False
            output = f"{result.stdout}\n{result.stderr}"
            return "cccc" in output
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    except Exception:
        pass
    return False


def ensure_mcp_installed(runtime: str, cwd: Path, *, auto_mcp_runtimes: tuple[str, ...]) -> bool:
    if runtime not in auto_mcp_runtimes:
        return True
    if is_mcp_installed(runtime):
        return True
    cccc_cmd = get_cccc_mcp_stdio_command()
    try:
        if runtime == "claude":
            result = subprocess.run(
                ["claude", "mcp", "add", "-s", "user", "cccc", "--", *cccc_cmd],
                capture_output=True,
                text=True,
                cwd=str(cwd),
                timeout=30,
            )
            return result.returncode == 0

        if runtime == "codex":
            result = subprocess.run(
                ["codex", "mcp", "add", "cccc", "--", *cccc_cmd],
                capture_output=True,
                text=True,
                cwd=str(cwd),
                timeout=30,
            )
            return result.returncode == 0 and is_mcp_installed("codex")

        if runtime == "droid":
            result = subprocess.run(
                ["droid", "mcp", "add", "--type", "stdio", "cccc", *cccc_cmd],
                capture_output=True,
                text=True,
                cwd=str(cwd),
                timeout=30,
            )
            return result.returncode == 0

        if runtime == "amp":
            result = subprocess.run(
                ["amp", "mcp", "add", "cccc", *cccc_cmd],
                capture_output=True,
                text=True,
                cwd=str(cwd),
                timeout=30,
            )
            return result.returncode == 0

        if runtime == "auggie":
            result = subprocess.run(
                ["auggie", "mcp", "add", "cccc", "--", *cccc_cmd],
                capture_output=True,
                text=True,
                cwd=str(cwd),
                timeout=30,
            )
            return result.returncode == 0

        if runtime == "neovate":
            result = subprocess.run(
                ["neovate", "mcp", "add", "-g", "cccc", *cccc_cmd],
                capture_output=True,
                text=True,
                cwd=str(cwd),
                timeout=30,
            )
            return result.returncode == 0

        if runtime == "gemini":
            result = subprocess.run(
                ["gemini", "mcp", "add", "-s", "user", "cccc", *cccc_cmd],
                capture_output=True,
                text=True,
                cwd=str(cwd),
                timeout=30,
            )
            return result.returncode == 0

        if runtime == "kimi":
            result = subprocess.run(
                ["kimi", "mcp", "add", "cccc", "--command", *cccc_cmd],
                capture_output=True,
                text=True,
                cwd=str(cwd),
                timeout=30,
            )
            return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return False
