"""Global settings management for CCCC.

Settings are stored in ~/.cccc/settings.yaml and include:
- runtime_pool: Prioritized list of agent runtimes with scenarios
- Other global preferences
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml  # type: ignore

from ..paths import ensure_home
from ..util.fs import atomic_write_text


@dataclass
class RuntimePoolEntry:
    """An entry in the runtime pool."""
    runtime: str
    priority: int
    scenarios: List[str] = field(default_factory=list)
    notes: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "runtime": self.runtime,
            "priority": self.priority,
            "scenarios": self.scenarios,
            "notes": self.notes,
        }
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RuntimePoolEntry":
        return cls(
            runtime=str(d.get("runtime") or ""),
            priority=int(d.get("priority") or 999),
            scenarios=list(d.get("scenarios") or []),
            notes=str(d.get("notes") or ""),
        )


# Default runtime pool (used if no settings.yaml exists)
DEFAULT_RUNTIME_POOL: List[RuntimePoolEntry] = [
    RuntimePoolEntry(
        runtime="claude",
        priority=1,
        scenarios=["coding", "review", "planning", "general"],
        notes="Strong coding; MCP support; good for complex tasks",
    ),
    RuntimePoolEntry(
        runtime="codex",
        priority=2,
        scenarios=["coding", "refactoring", "sandbox"],
        notes="Good sandbox support; suitable for risky operations",
    ),
    RuntimePoolEntry(
        runtime="droid",
        priority=3,
        scenarios=["coding", "long-session"],
        notes="Robust auto mode; good for long tasks",
    ),
    RuntimePoolEntry(
        runtime="amp",
        priority=4,
        scenarios=["coding", "review", "general"],
        notes="Amp CLI; MCP support",
    ),
    RuntimePoolEntry(
        runtime="neovate",
        priority=5,
        scenarios=["coding", "general"],
        notes="Neovate Code; MCP support",
    ),
    RuntimePoolEntry(
        runtime="opencode",
        priority=6,
        scenarios=["coding"],
        notes="Solid coding CLI; steady long sessions",
    ),
    RuntimePoolEntry(
        runtime="copilot",
        priority=7,
        scenarios=["coding", "general"],
        notes="GitHub Copilot CLI; MCP support",
    ),
    RuntimePoolEntry(
        runtime="gemini",
        priority=8,
        scenarios=["coding", "general"],
        notes="Gemini CLI; MCP support",
    ),
    RuntimePoolEntry(
        runtime="auggie",
        priority=9,
        scenarios=["coding", "general"],
        notes="Auggie (Augment CLI); MCP support",
    ),
    RuntimePoolEntry(
        runtime="cursor",
        priority=10,
        scenarios=["coding", "general"],
        notes="Cursor CLI (cursor-agent); MCP support",
    ),
    RuntimePoolEntry(
        runtime="kilocode",
        priority=11,
        scenarios=["coding", "general"],
        notes="Kilo Code CLI; MCP support",
    ),
]


def _settings_path() -> Path:
    return ensure_home() / "settings.yaml"


def load_settings() -> Dict[str, Any]:
    """Load global settings from ~/.cccc/settings.yaml."""
    p = _settings_path()
    if not p.exists():
        return {}
    try:
        doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def save_settings(settings: Dict[str, Any]) -> None:
    """Save global settings to ~/.cccc/settings.yaml."""
    p = _settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(p, yaml.safe_dump(settings, allow_unicode=True, sort_keys=False))


def get_runtime_pool() -> List[RuntimePoolEntry]:
    """Get the runtime pool from settings, or default if not configured."""
    settings = load_settings()
    pool_raw = settings.get("runtime_pool")
    
    if not isinstance(pool_raw, list) or not pool_raw:
        return DEFAULT_RUNTIME_POOL
    
    pool: List[RuntimePoolEntry] = []
    for item in pool_raw:
        if isinstance(item, dict) and item.get("runtime"):
            pool.append(RuntimePoolEntry.from_dict(item))
    
    if not pool:
        return DEFAULT_RUNTIME_POOL
    
    # Sort by priority
    pool.sort(key=lambda x: x.priority)
    return pool


def set_runtime_pool(pool: List[RuntimePoolEntry]) -> None:
    """Set the runtime pool in settings."""
    settings = load_settings()
    settings["runtime_pool"] = [e.to_dict() for e in pool]
    save_settings(settings)


def get_recommended_runtime(scenario: str = "general") -> Optional[str]:
    """Get the recommended runtime for a scenario.
    
    Returns the highest priority available runtime that matches the scenario.
    """
    from .runtime import detect_runtime
    
    pool = get_runtime_pool()
    scenario_lower = scenario.lower()
    
    # First pass: find matching scenario
    for entry in pool:
        if scenario_lower in [s.lower() for s in entry.scenarios]:
            rt = detect_runtime(entry.runtime)
            if rt.available:
                return entry.runtime
    
    # Second pass: any available runtime
    for entry in pool:
        rt = detect_runtime(entry.runtime)
        if rt.available:
            return entry.runtime
    
    return None
