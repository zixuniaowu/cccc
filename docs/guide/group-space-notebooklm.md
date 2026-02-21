# Group Space + NotebookLM (Web)

This guide covers the fastest way to validate real Group Space behavior from the Web UI.

## 1. Enable Real Provider Path

Start CCCC with real NotebookLM adapter enabled:

```bash
export CCCC_NOTEBOOKLM_REAL=1
cccc
```

If you expose Web outside localhost, also set a web token:

```bash
export CCCC_WEB_TOKEN="change-me"
```

## 2. Open Group Space Settings

1. Open a target group in Web.
2. Open **Settings**.
3. Open **Group Space** tab.

## 3. Configure Provider Credential

In **Provider Credential**:

1. Paste NotebookLM auth JSON (`storage_state`-style payload with `cookies`).
2. Click **Save Credential**.
3. Click **Health Check**.

Expected result:

- Health check returns success.
- Credential view shows masked metadata only (never plaintext).

## 4. Bind Group to Notebook

In **Binding**:

1. Fill `remote_space_id` with your NotebookLM notebook id (optional).
   - If empty, CCCC will auto-create a notebook and bind it.
2. Click **Bind**.

Expected result:

- Binding status becomes `bound`.
- Provider mode stays `active` when healthy.

## 5. Verify Ingest + Query

In **Ingest**:

1. Use `context_sync` or `resource_ingest`.
2. Submit payload JSON.

In **Query**:

1. Ask a short query.
2. Confirm answer/degraded status.

## 6. Failure Behavior (Important)

Provider failures should degrade Group Space, not break core CCCC chat/actor workflows.

Common error codes:

- `space_provider_not_configured`
- `space_provider_auth_invalid`
- `space_provider_compat_mismatch`
- `space_provider_timeout`

## 7. Quick Rollback

If NotebookLM path is unstable in your environment:

```bash
unset CCCC_NOTEBOOKLM_REAL
cccc daemon restart
```

You can also clear stored provider credential from Group Space settings.

## 8. Triage Checklist

Use this order when Group Space behaves unexpectedly:

1. `Health Check` in Web Group Space tab.
2. Confirm provider mode (`active`/`degraded`/`disabled`).
3. Confirm group binding is `bound` and `remote_space_id` is correct.
4. Check jobs list (`failed` state and `last_error`).
5. If upstream remains unstable, rollback to non-real mode and continue core workflows.

## 9. Throughput Guard (Optional)

Group Space provider writes are globally capped to protect upstream rate limits.

Default cap: `2` in-flight writes.

Tune only when necessary:

```bash
export CCCC_SPACE_PROVIDER_MAX_INFLIGHT=1   # safer / slower
export CCCC_SPACE_PROVIDER_MAX_INFLIGHT=4   # faster / higher upstream pressure
```

## 10. Repo Space Sync

When a group has a local scope attached, CCCC uses repo-local `space/` as the resource source of truth:

`<scope_root>/space/`

Sync metadata files:

- `<scope_root>/space/.space-index.json` (path/hash -> remote source mapping)
- `<scope_root>/space/.space-sync-state.json` (last sync run and convergence state)
- `<scope_root>/space/.space-status.json` (status snapshot for UI/debug)

You can force a full reconcile from CLI:

```bash
cccc space sync --force
```
