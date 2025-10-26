from pathlib import Path

__all__ = ["run_app"]


def run_app(home: Path) -> None:
    from .app import run  # lazy import to keep startup fast
    run(home)

