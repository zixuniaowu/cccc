#!/usr/bin/env python3
"""
Verify /quit command implementation in app.py
"""

import sys
from pathlib import Path

def verify_quit_implementation():
    """Check all 4 locations where /quit should be implemented"""

    app_py = Path('.cccc/tui_ptk/app.py')
    if not app_py.exists():
        print(f"❌ app.py not found at {app_py}")
        return False

    content = app_py.read_text()
    lines = content.split('\n')

    checks = {
        'header_doc': False,
        'command_completer': False,
        'help_output': False,
        'process_command': False,
        'quit_app_exists': False
    }

    # Check 1: Documentation header (line ~11)
    for i, line in enumerate(lines[:20]):
        if 'Commands:' in line and '/quit' in line:
            checks['header_doc'] = True
            print(f"✓ Line {i+1}: Documentation header includes /quit")
            break

    # Check 2: CommandCompleter (line ~88)
    for i, line in enumerate(lines[80:100], start=81):
        if "('/quit', 'Quit CCCC')" in line:
            checks['command_completer'] = True
            print(f"✓ Line {i}: CommandCompleter includes /quit")
            break

    # Check 3: /help output (line ~2003)
    for i, line in enumerate(lines[1990:2020], start=1991):
        if '/quit' in line and 'exit all processes' in line:
            checks['help_output'] = True
            print(f"✓ Line {i}: /help output includes /quit description")
            break

    # Check 4: _process_command handler (line ~2038)
    for i, line in enumerate(lines[2030:2050], start=2031):
        if "text == '/quit'" in line and "_quit_app()" in lines[i+1]:
            checks['process_command'] = True
            print(f"✓ Line {i}: _process_command handles /quit")
            break

    # Check 5: _quit_app method exists
    for i, line in enumerate(lines[1560:1600], start=1561):
        if 'def _quit_app(self)' in line:
            checks['quit_app_exists'] = True
            print(f"✓ Line {i}: _quit_app() method exists")
            break

    # Summary
    print("\n=== Verification Summary ===")
    all_passed = all(checks.values())

    for check_name, passed in checks.items():
        status = "✓" if passed else "✗"
        print(f"{status} {check_name}: {'PASS' if passed else 'FAIL'}")

    if all_passed:
        print("\n✓ All checks passed! /quit command is fully implemented.")
        return True
    else:
        print("\n✗ Some checks failed. Please review the implementation.")
        return False

if __name__ == '__main__':
    success = verify_quit_implementation()
    sys.exit(0 if success else 1)
