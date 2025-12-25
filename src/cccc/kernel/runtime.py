"""Runtime detection and configuration for agent CLIs."""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import Dict, List, Optional


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


# Known agent runtimes with their configurations
KNOWN_RUNTIMES: Dict[str, Dict[str, str]] = {
    "claude": {
        "display_name": "Claude Code",
        "command": "claude",
        "capabilities": "Strong coding; MCP support; no built-in web browsing",
        "mcp_add_pattern": "claude mcp add {name} -s project -- {cmd}",
    },
    "codex": {
        "display_name": "Codex CLI",
        "command": "codex",
        "capabilities": "Strong coding; multimodal input; sandbox support",
        "mcp_add_pattern": "codex mcp add {name} -- {cmd}",
    },
    "droid": {
        "display_name": "Droid CLI",
        "command": "droid",
        "capabilities": "Strong coding; robust auto mode; good long sessions",
        "mcp_add_pattern": "droid mcp add {name} -- {cmd}",
    },
    "opencode": {
        "display_name": "OpenCode",
        "command": "opencode",
        "capabilities": "Solid coding CLI; steady long sessions",
        "mcp_add_pattern": None,  # Requires manual config
    },
    "gemini": {
        "display_name": "Gemini CLI",
        "command": "gemini",
        "capabilities": "Strong coding; web searching; large context; image support",
        "mcp_add_pattern": None,
    },
    "copilot": {
        "display_name": "GitHub Copilot CLI",
        "command": "copilot",
        "capabilities": "GitHub Copilot CLI with tool access; integrated with GitHub",
        "mcp_add_pattern": None,
    },
    "cursor": {
        "display_name": "Cursor Agent",
        "command": "cursor-agent",
        "capabilities": "Cursor AI agent; strong coding; editor-integrated",
        "mcp_add_pattern": None,
    },
    "auggie": {
        "display_name": "Augment Code",
        "command": "auggie",
        "capabilities": "Augment Code AI assistant; solid coding support",
        "mcp_add_pattern": None,
    },
    "kilocode": {
        "display_name": "KiloCode",
        "command": "kilocode",
        "capabilities": "AI-powered coding assistant with autonomous capabilities",
        "mcp_add_pattern": None,
    },
}

# Primary supported runtimes (with full MCP integration)
PRIMARY_RUNTIMES = ["claude", "codex", "droid", "opencode"]


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
        primary_only: If True, only check primary supported runtimes (claude, codex, droid, opencode).
                     If False, check all known runtimes.
    
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
        "codex": ["codex", "--dangerously-bypass-approvals-and-sandbox"],
        "droid": ["droid", "--auto", "high"],
        "opencode": ["opencode"],
        "gemini": ["gemini", "--yolo"],
        "copilot": ["copilot", "--allow-all-tools"],
        "cursor": ["cursor-agent"],
        "auggie": ["auggie"],
        "kilocode": ["kilocode", "--auto"],
    }
    return commands.get(name, [name])
