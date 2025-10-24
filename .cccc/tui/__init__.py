from pathlib import Path

__all__ = [
    "run_app",
]

def run_app(home: Path) -> None:
    # Import lazily to keep import time minimal for the orchestrator
    from .app import CCCCApp  # noqa: WPS433
    app = CCCCApp(home)
    app.run()

