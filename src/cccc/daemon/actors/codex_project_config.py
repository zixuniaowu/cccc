from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import tomllib
except Exception:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]

from ...util.fs import atomic_write_text

_MANAGED_HEADER = "# Managed by CCCC for per-project Codex runtime config."
_DEFAULT_MODEL = "gpt-5.4"
_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_PROVIDER_ID = "cccc_openai"


def _normalize_base_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return _DEFAULT_BASE_URL
    return text.rstrip("/")


def _normalize_scope_root(path: str) -> Optional[Path]:
    raw = str(path or "").strip()
    if not raw:
        return None
    try:
        root = Path(raw).expanduser().resolve()
    except Exception:
        return None
    return root if root.exists() and root.is_dir() else None


def _slugify_provider_id(name: str) -> str:
    text = str(name or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "project"


def _default_provider_identity(root: Path) -> tuple[str, str]:
    display_name = root.name.strip() or "Project"
    slug = _slugify_provider_id(display_name)
    digest = hashlib.sha1(str(root).encode("utf-8")).hexdigest()[:6]
    provider_id = slug if slug not in {"openai", "anthropic", "gemini"} else f"{slug}-{digest}"
    return provider_id, display_name


def _serialize_string(value: str) -> str:
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _render_config(*, model: str, provider_id: str, provider_name: str, base_url: str, env_key: str) -> str:
    lines = [
        _MANAGED_HEADER,
        f"model = {_serialize_string(model)}",
        f"model_provider = {_serialize_string(provider_id)}",
        "",
        f"[model_providers.{provider_id}]",
        f"name = {_serialize_string(provider_name)}",
        f"base_url = {_serialize_string(base_url)}",
        'wire_api = "responses"',
        f"env_key = {_serialize_string(env_key)}",
        "",
    ]
    return "\n".join(lines)


def sync_codex_project_config_for_scope(
    *,
    scope_root: str,
    private_env: Dict[str, str],
    actor: Dict[str, Any],
) -> Optional[str]:
    runtime = str(actor.get("runtime") or "codex").strip() or "codex"
    if runtime != "codex":
        return None
    api_key_name = "OPENAI_API_KEY"
    if not str(private_env.get(api_key_name) or "").strip():
        return None
    root = _normalize_scope_root(scope_root)
    if root is None:
        return None

    provider_id, provider_name = _default_provider_identity(root)
    model = str(actor.get("model") or _DEFAULT_MODEL).strip() or _DEFAULT_MODEL
    base_url = _normalize_base_url(private_env.get("OPENAI_BASE_URL"))

    codex_dir = root / ".codex"
    config_path = codex_dir / "config.toml"
    if config_path.exists():
        try:
            existing = config_path.read_text(encoding="utf-8")
        except Exception:
            existing = ""
        if existing.strip() and not existing.startswith(_MANAGED_HEADER):
            return None
        if tomllib is not None and existing.strip():
            try:
                parsed = tomllib.loads(existing)
            except Exception:
                parsed = {}
            if isinstance(parsed, dict):
                model = str(parsed.get("model") or model).strip() or model
                existing_provider_id = str(parsed.get("model_provider") or "").strip()
                if existing_provider_id:
                    provider_id = existing_provider_id
                providers = parsed.get("model_providers")
                provider = providers.get(provider_id) if isinstance(providers, dict) else None
                if isinstance(provider, dict):
                    provider_name = str(provider.get("name") or provider_name).strip() or provider_name
                    env_key = str(provider.get("env_key") or api_key_name).strip() or api_key_name
                    if env_key != api_key_name:
                        return None
    codex_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        config_path,
        _render_config(
            model=model,
            provider_id=provider_id,
            provider_name=provider_name,
            base_url=base_url,
            env_key=api_key_name,
        ),
        encoding="utf-8",
    )
    return str(config_path)
