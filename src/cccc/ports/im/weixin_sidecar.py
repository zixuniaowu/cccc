from __future__ import annotations

from pathlib import Path

from ...paths import ensure_home
from ...util.fs import atomic_write_bytes


_SIDECAR_CACHE_DIR = ("cache", "sidecars")
_SIDECAR_FILENAME = "weixin_sidecar.mjs"


def _repo_sidecar_path() -> Path:
    return Path(__file__).resolve().parents[4] / "scripts" / "im" / _SIDECAR_FILENAME


def _packaged_sidecar_bytes() -> bytes:
    try:
        import importlib.resources

        files = importlib.resources.files("cccc.resources")
        return files.joinpath("im").joinpath(_SIDECAR_FILENAME).read_bytes()
    except Exception:
        return b""


def resolve_weixin_sidecar_script_path() -> Path:
    """Resolve a stable filesystem path for the bundled weixin sidecar script."""
    repo_script = _repo_sidecar_path()
    if repo_script.exists():
        return repo_script

    packaged = _packaged_sidecar_bytes()
    if packaged:
        cache_path = ensure_home().joinpath(*_SIDECAR_CACHE_DIR, _SIDECAR_FILENAME)
        try:
            current = cache_path.read_bytes() if cache_path.exists() else b""
        except Exception:
            current = b""
        if current != packaged:
            atomic_write_bytes(cache_path, packaged)
        return cache_path

    return repo_script
