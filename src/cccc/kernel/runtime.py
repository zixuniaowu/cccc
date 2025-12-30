"""Runtime detection and configuration for agent CLIs."""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class RuntimeInfo:
    """Information about an agent runtime."""
    name: str
    display_name: str
    command: str
    available: bool
    path: Optional[str]
    capabilities: str
    mcp_add_command: Optional[List[str]]  # Command to add MCP server, None if manual config required
    # From runtime pool (if configured)
    priority: int = 999
    scenarios: List[str] = None  # type: ignore
    
    def __post_init__(self) -> None:
        if self.scenarios is None:
            self.scenarios = []


# Known agent runtimes with their configurations
KNOWN_RUNTIMES: Dict[str, Dict[str, Any]] = {
    "claude": {
        "display_name": "Claude Code",
        "command": "claude",
        "capabilities": "MCP; MCP setup: auto",
        "mcp_add_pattern": "claude mcp add -s user {name} -- {cmd}",
    },
    "codex": {
        "display_name": "Codex CLI",
        "command": "codex",
        "capabilities": "MCP; MCP setup: auto",
        "mcp_add_pattern": "codex mcp add {name} -- {cmd}",
    },
    "droid": {
        "display_name": "Droid CLI",
        "command": "droid",
        "capabilities": "MCP; MCP setup: auto",
        "mcp_add_pattern": "droid mcp add {name} -- {cmd}",
    },
    "opencode": {
        "display_name": "OpenCode",
        "command": "opencode",
        "capabilities": "MCP; MCP setup: manual",
        "mcp_add_pattern": None,  # Requires manual config
    },
    "copilot": {
        "display_name": "GitHub Copilot CLI",
        "command": "copilot",
        "capabilities": "MCP; MCP setup: manual",
        "mcp_add_pattern": None,
    },
}

# Primary supported runtimes (auto MCP installation)
PRIMARY_RUNTIMES = ["claude", "codex", "droid"]


def detect_runtime(name: str) -> RuntimeInfo:
    """Detect if a specific runtime is available on the system."""
    config = KNOWN_RUNTIMES.get(name)
    if not config:
        return RuntimeInfo(
            name=name,
            display_name=name,
            command=name,
            available=False,
            path=None,
            capabilities="Unknown runtime",
            mcp_add_command=None,
        )
    
    command = config["command"]
    path = shutil.which(command)
    available = path is not None
    
    mcp_add_command = None
    if config.get("mcp_add_pattern") and available:
        # Parse the pattern into a command list
        # This is just for reference; actual execution happens elsewhere
        mcp_add_command = config["mcp_add_pattern"].split()
    
    return RuntimeInfo(
        name=name,
        display_name=config["display_name"],
        command=command,
        available=available,
        path=path,
        capabilities=config["capabilities"],
        mcp_add_command=mcp_add_command,
    )


def detect_all_runtimes(primary_only: bool = True) -> List[RuntimeInfo]:
    """Detect all known runtimes on the system.
    
    Args:
        primary_only: If True, only check auto-setup runtimes (claude, codex, droid).
                     If False, check all supported runtimes (including manual MCP ones).
    
    Returns:
        List of RuntimeInfo for each runtime.
    """
    names = PRIMARY_RUNTIMES if primary_only else list(KNOWN_RUNTIMES.keys())
    return [detect_runtime(name) for name in names]


def get_available_runtimes(primary_only: bool = True) -> List[RuntimeInfo]:
    """Get only the runtimes that are available on the system."""
    return [r for r in detect_all_runtimes(primary_only) if r.available]


def get_runtime_command(name: str) -> List[str]:
    """Get the default command for a runtime.
    
    Returns the command as a list suitable for subprocess.
    """
    config = KNOWN_RUNTIMES.get(name)
    if not config:
        return [name]
    return [config["command"]]


def get_runtime_command_with_flags(name: str) -> List[str]:
    """Get the command with recommended flags for autonomous operation."""
    commands = {
        "claude": ["claude", "--dangerously-skip-permissions"],
        "codex": ["codex", "--dangerously-bypass-approvals-and-sandbox", "--search"],
        "droid": ["droid", "--auto", "high"],
        "opencode": ["opencode"],
        "copilot": ["copilot", "--allow-all-tools", "--allow-all-paths"],
    }
    return commands.get(name, [name])
