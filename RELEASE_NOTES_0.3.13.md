# Release 0.3.13 - Delivery & Context Refresh Optimization

**Release Date**: 2025-01-13

## Overview

This release focuses on optimizing the orchestrator's message delivery system and context refresh mechanisms, reducing unnecessary overhead and improving peer context consistency.

## Key Improvements

### 🚀 Delivery System Optimization

**Removed obsolete wait_for_ready heuristic**
- Eliminated the legacy pane readiness check that almost always timed out (12s)
- Removed frequent `[WARN] Target pane not ready; pasting anyway` warnings from orchestrator logs
- Message delivery now relies directly on `send_text` with bracketed paste or char-by-char typing
- **Impact**: Cleaner logs, faster message delivery, no change to reliability

### 📋 POR Refresh Frequency Fix

**Fixed double-trigger issue for POR updates**
- Previously: Both PeerA and PeerB's SYSTEM injection cycles triggered POR refresh to PeerB
- Now: Only PeerB's SYSTEM injection triggers POR refresh (since POR is owned by PeerB)
- **Impact**: 50% reduction in POR refresh frequency, eliminating redundant interruptions

### 📚 PROJECT.md Startup Injection

**Enhanced peer context at initialization**
- PROJECT.md (vision/constraints/non-goals) now injected during lazy preamble at startup
- Previously only injected at Kth self-check, causing peers to miss critical context early on
- Matches the format used in periodic SYSTEM refreshes for consistency
- **Impact**: Fewer early mistakes from peers, better alignment with project goals from the start

### 🎯 Unified Context Refresh Cadence

**Aligned POR refresh with SYSTEM injection**
- POR refresh requests now only trigger during full SYSTEM injection cycles
- Moved from "every self-check" to "every Kth self-check" (configurable via `system_refresh_every_self_checks`)
- **Impact**: More coherent timing, POR updates synchronized with full context refresh

### 🔧 Mailbox Path Clarification

**Fixed ambiguous processed directory path**
- Nudge messages now use absolute path `.cccc/mailbox/peerX/processed` instead of relative `processed/.`
- Eliminates confusion where peers placed processed files in wrong directories
- **Impact**: Consistent mailbox file organization, no more misplaced files

## Configuration Notes

- Default `self_check_every_handoffs: 6` (trigger self-check every 6 handoffs)
- Default `system_refresh_every_self_checks: 2` (full SYSTEM injection every 2 self-checks = every 12 handoffs)
- POR refresh now aligned with SYSTEM refresh cycle (every 12 handoffs for PeerB only)

## Technical Details

**Files Modified**:
- `.cccc/orchestrator_tmux.py` - Removed `wait_for_ready` check from `paste_when_ready`
- `.cccc/orchestrator/handoff.py` - Added PeerB-only guard for POR refresh, moved call inside SYSTEM injection block
- `.cccc/prompt_weaver.py` - Added PROJECT.md injection in `weave_system_prompt`
- `.cccc/orchestrator/handoff_helpers.py` - Changed relative path to absolute path in nudge action text

## Upgrade Notes

No breaking changes. All modifications are internal optimizations that maintain backward compatibility.

## What's Next

- Future consideration: Further simplification of PaneIdleJudge (currently semi-deprecated)
- Monitoring: Verify POR refresh frequency reduction in production workloads
- Documentation: Update operational guide with new delivery semantics

---

**Full Changelog**: https://github.com/ChesterRa/cccc/compare/v0.3.12...v0.3.13
