from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from ..paths import ensure_home
from ..util.fs import atomic_write_json, read_json
from ..util.time import utc_now_iso


@dataclass
class Registry:
    path: Path
    doc: Dict[str, Any]

    @property
    def groups(self) -> Dict[str, Any]:
        d = self.doc.setdefault("groups", {})
        return d if isinstance(d, dict) else {}

    @property
    def defaults(self) -> Dict[str, str]:
        d = self.doc.setdefault("defaults", {})
        return d if isinstance(d, dict) else {}

    def save(self) -> None:
        self.doc.setdefault("v", 1)
        self.doc["updated_at"] = utc_now_iso()
        atomic_write_json(self.path, self.doc)


def load_registry() -> Registry:
    home = ensure_home()
    path = home / "registry.json"
    doc = read_json(path)
    if not doc:
        doc = {"v": 1, "created_at": utc_now_iso(), "updated_at": utc_now_iso(), "groups": {}, "defaults": {}}
        atomic_write_json(path, doc)
    return Registry(path=path, doc=doc)


def default_group_id_for_scope(reg: Registry, scope_key: str) -> Optional[str]:
    return reg.defaults.get(scope_key) or None


def set_default_group_for_scope(reg: Registry, scope_key: str, group_id: str) -> None:
    reg.defaults[scope_key] = group_id
    reg.save()

