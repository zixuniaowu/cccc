# -*- coding: utf-8 -*-
from pathlib import Path
from typing import Dict, Any
import json, re

BAR = "\n" + ("-"*60) + "\n"

def _read_text(p: Path) -> str:
    if not p.exists(): raise FileNotFoundError(f"缺少: {p}")
    return p.read_text(encoding="utf-8")

def _read_yaml(p: Path) -> Dict[str, Any]:
    if not p.exists(): return {}
    try:
        import yaml; return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except ImportError:
        d={}; 
        for line in p.read_text(encoding="utf-8").splitlines():
            line=line.strip()
            if not line or line.startswith("#") or ":" not in line: continue
            k,v=line.split(":",1); d[k.strip()]=v.strip().strip('"\'')
        return d

def _substitute(text: str, mapping: Dict[str, Any]) -> str:
    def rep(m):
        k=m.group(1).strip()
        return str(mapping.get(k, m.group(0)))
    return re.sub(r"\{\{([A-Za-z0-9_\-\.]+)\}\}", rep, text)

def weave_system_prompt(home: Path, peer: str) -> str:
    prompts  = home/"prompts"
    personas = home/"personas"
    settings = home/"settings"

    core    = _read_text(prompts / f"{peer}.core.txt")
    persona = _read_text(personas / f"{peer}.persona.txt")
    guard   = _read_text(prompts / "shared.guardrails.txt")

    traits_all = _read_yaml(settings / "traits.yaml")
    policies   = _read_yaml(settings / "policies.yaml")
    traits = traits_all.get(peer, traits_all.get(peer.lower(), {}))

    persona = _substitute(persona, traits)
    policies_excerpt = {
        "patch_queue": policies.get("patch_queue", {}),
        "rfd":         policies.get("rfd", {}),
        "autonomy_level": policies.get("autonomy_level")
    }

    system = (
        core.strip()
        + BAR + "# PERSONA\n" + persona.strip()
        + BAR + "# GUARDRAILS\n" + guard.strip()
        + BAR + "# TRAITS\n" + json.dumps(traits, ensure_ascii=False)
        + BAR + "# POLICIES (excerpt)\n" + json.dumps(policies_excerpt, ensure_ascii=False)
        + BAR + "# MESSAGE CONTRACT\n"
        + "必须严格用 <TO_USER> 与 <TO_PEER> 两段输出；如需提交代码，仅输出一个 ```patch ...``` 围栏（统一 diff）。\n"
        + "不得泄露冗长思维链；只提供理由摘要与事实引用。\n"
    )
    return system
