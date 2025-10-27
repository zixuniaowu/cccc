#!/usr/bin/env python3
"""
TUI Diagnostics Script - Check all potential startup failures
"""
import sys
import os
from pathlib import Path

print("=" * 60)
print("CCCC TUI Startup Diagnostics")
print("=" * 60)

# 1. Python environment
print("\n1. Python Environment:")
print(f"   Executable: {sys.executable}")
in_venv = hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix)
print(f"   In venv: {in_venv}")
print(f"   Version: {sys.version}")

# 2. Check required modules
print("\n2. Required Modules:")
modules_to_check = ['prompt_toolkit', 'yaml', 'asyncio', 'json', 'pathlib']
for mod in modules_to_check:
    try:
        __import__(mod)
        print(f"   ✓ {mod}")
    except ImportError as e:
        print(f"   ✗ {mod} - {e}")

# 3. Check prompt_toolkit version (if available)
try:
    import prompt_toolkit
    print(f"\n3. prompt_toolkit version: {prompt_toolkit.__version__}")
    from prompt_toolkit import Application
    from prompt_toolkit.widgets import TextArea
    print("   ✓ Key imports successful")
except Exception as e:
    print(f"\n3. prompt_toolkit import failed: {e}")

# 4. Check tui_ptk package structure
print("\n4. TUI Package Structure:")
cccc_dir = Path(__file__).parent
tui_dir = cccc_dir / "tui_ptk"
init_file = tui_dir / "__init__.py"
app_file = tui_dir / "app.py"

print(f"   CCCC dir: {cccc_dir}")
print(f"   TUI dir exists: {tui_dir.exists()}")
print(f"   __init__.py exists: {init_file.exists()}")
print(f"   app.py exists: {app_file.exists()}")

if app_file.exists():
    size = app_file.stat().st_size
    print(f"   app.py size: {size} bytes")

# 5. Try importing tui_ptk package
print("\n5. Importing tui_ptk package:")
try:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "tui_ptk_diag", str(init_file),
        submodule_search_locations=[str(tui_dir)]
    )
    if not spec or not spec.loader:
        print("   ✗ Cannot create module spec")
    else:
        pkg = importlib.util.module_from_spec(spec)
        sys.modules["tui_ptk_diag"] = pkg
        spec.loader.exec_module(pkg)
        print("   ✓ Package imported (lazy import)")

        # Try calling run_app (this will trigger actual import)
        print("\n6. Calling run_app (triggers app.py import):")
        try:
            run_app = getattr(pkg, 'run_app')
            # Don't actually run it, just try to import app module
            from importlib import import_module
            sys.modules["tui_ptk_diag.app"] = None  # Prevent actual import
            print("   Note: Would call run_app(home) here")
            print("   This would import app.py and fail if prompt_toolkit missing")
        except Exception as e:
            print(f"   ✗ Error accessing run_app: {e}")

except Exception as e:
    import traceback
    print(f"   ✗ Import failed: {e}")
    traceback.print_exc()

# 7. Check file permissions
print("\n7. File Permissions:")
state_dir = cccc_dir / "state"
print(f"   State dir: {state_dir}")
print(f"   Exists: {state_dir.exists()}")
if state_dir.exists():
    print(f"   Writable: {os.access(state_dir, os.W_OK)}")

settings_dir = cccc_dir / "settings"
print(f"   Settings dir: {settings_dir}")
print(f"   Exists: {settings_dir.exists()}")
if settings_dir.exists():
    print(f"   Readable: {os.access(settings_dir, os.R_OK)}")

# 8. Summary
print("\n" + "=" * 60)
print("DIAGNOSIS SUMMARY:")
print("=" * 60)

issues = []
if not in_venv:
    issues.append("• Not running in virtual environment (.venv)")

try:
    import prompt_toolkit
except ImportError:
    issues.append("• prompt_toolkit not installed in current Python")

if issues:
    print("\n⚠️  Issues found:")
    for issue in issues:
        print(f"   {issue}")
    print("\n💡 Recommended solutions:")
    print("   1. Activate virtual environment: source .venv/bin/activate")
    print("   2. Then run cccc: python cccc.py run")
    print("   OR")
    print("   3. Install prompt_toolkit in system Python: pip install prompt_toolkit>=3.0.52")
else:
    print("\n✓ No obvious issues found")
    print("  TUI should be able to start successfully")

print("=" * 60)
