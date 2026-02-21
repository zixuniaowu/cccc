from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch


class TestGroupSpaceOps(unittest.TestCase):
    def _with_home(self):
        old_home = os.environ.get("CCCC_HOME")
        td_ctx = tempfile.TemporaryDirectory()
        td = td_ctx.__enter__()
        os.environ["CCCC_HOME"] = td

        def cleanup() -> None:
            td_ctx.__exit__(None, None, None)
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home

        return td, cleanup

    def _with_env(self, key: str, value: str | None):
        old = os.environ.get(key)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

        def cleanup() -> None:
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old

        return cleanup

    def _call(self, op: str, args: dict):
        from cccc.contracts.v1 import DaemonRequest
        from cccc.daemon.server import handle_request

        return handle_request(DaemonRequest.model_validate({"op": op, "args": args}))

    def _create_group(self, title: str = "space-test") -> str:
        create, _ = self._call("group_create", {"title": title, "topic": "", "by": "user"})
        self.assertTrue(create.ok, getattr(create, "error", None))
        gid = str((create.result or {}).get("group_id") or "").strip()
        self.assertTrue(gid)
        return gid

    def _add_actor(self, group_id: str, actor_id: str, *, by: str = "user") -> None:
        add, _ = self._call(
            "actor_add",
            {
                "group_id": group_id,
                "actor_id": actor_id,
                "runtime": "codex",
                "runner": "headless",
                "by": by,
            },
        )
        self.assertTrue(add.ok, getattr(add, "error", None))

    def test_status_defaults_to_unbound(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            status, _ = self._call("group_space_status", {"group_id": gid})
            self.assertTrue(status.ok, getattr(status, "error", None))
            result = status.result if isinstance(status.result, dict) else {}
            provider = result.get("provider") if isinstance(result.get("provider"), dict) else {}
            binding = result.get("binding") if isinstance(result.get("binding"), dict) else {}
            summary = result.get("queue_summary") if isinstance(result.get("queue_summary"), dict) else {}
            self.assertEqual(str(provider.get("provider") or ""), "notebooklm")
            self.assertEqual(str(provider.get("mode") or ""), "disabled")
            self.assertEqual(bool(provider.get("enabled")), False)
            self.assertEqual(str(binding.get("status") or ""), "unbound")
            self.assertEqual(int(summary.get("pending") or 0), 0)
        finally:
            cleanup()

    def test_bind_ingest_query_jobs_with_stub(self) -> None:
        _, cleanup = self._with_home()
        cleanup_stub = self._with_env("CCCC_NOTEBOOKLM_STUB", "1")
        try:
            gid = self._create_group()
            bind, _ = self._call(
                "group_space_bind",
                {
                    "group_id": gid,
                    "provider": "notebooklm",
                    "action": "bind",
                    "remote_space_id": "nb_123",
                    "by": "user",
                },
            )
            self.assertTrue(bind.ok, getattr(bind, "error", None))

            ingest_1, _ = self._call(
                "group_space_ingest",
                {
                    "group_id": gid,
                    "provider": "notebooklm",
                    "kind": "context_sync",
                    "payload": {"vision": "shipping v1"},
                    "idempotency_key": "sync-1",
                    "by": "user",
                },
            )
            self.assertTrue(ingest_1.ok, getattr(ingest_1, "error", None))
            r1 = ingest_1.result if isinstance(ingest_1.result, dict) else {}
            self.assertEqual(bool(r1.get("deduped")), False)
            job_1 = r1.get("job") if isinstance(r1.get("job"), dict) else {}
            self.assertEqual(str(job_1.get("state") or ""), "succeeded")
            job_id = str(r1.get("job_id") or "")
            self.assertTrue(job_id)

            ingest_2, _ = self._call(
                "group_space_ingest",
                {
                    "group_id": gid,
                    "provider": "notebooklm",
                    "kind": "context_sync",
                    "payload": {"vision": "shipping v1"},
                    "idempotency_key": "sync-1",
                    "by": "user",
                },
            )
            self.assertTrue(ingest_2.ok, getattr(ingest_2, "error", None))
            r2 = ingest_2.result if isinstance(ingest_2.result, dict) else {}
            self.assertEqual(bool(r2.get("deduped")), True)
            self.assertEqual(str(r2.get("job_id") or ""), job_id)

            query, _ = self._call(
                "group_space_query",
                {
                    "group_id": gid,
                    "provider": "notebooklm",
                    "query": "What is the current vision?",
                },
            )
            self.assertTrue(query.ok, getattr(query, "error", None))
            q = query.result if isinstance(query.result, dict) else {}
            self.assertEqual(bool(q.get("degraded")), False)
            self.assertIn("NotebookLM stub", str(q.get("answer") or ""))

            jobs, _ = self._call(
                "group_space_jobs",
                {"group_id": gid, "provider": "notebooklm", "action": "list"},
            )
            self.assertTrue(jobs.ok, getattr(jobs, "error", None))
            jobs_list = (jobs.result or {}).get("jobs") if isinstance(jobs.result, dict) else []
            self.assertIsInstance(jobs_list, list)
            self.assertGreaterEqual(len(jobs_list), 1)
        finally:
            cleanup_stub()
            cleanup()

    def test_bind_write_requires_permission(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group("space-perm")
            self._add_actor(gid, "foreman1", by="user")
            self._add_actor(gid, "peer1", by="user")

            denied, _ = self._call(
                "group_space_bind",
                {
                    "group_id": gid,
                    "provider": "notebooklm",
                    "action": "bind",
                    "remote_space_id": "nb_perm",
                    "by": "peer1",
                },
            )
            self.assertFalse(denied.ok)
            self.assertEqual(str(getattr(denied.error, "code", "")), "space_permission_denied")
        finally:
            cleanup()

    def test_degraded_mode_does_not_crash_query_flow(self) -> None:
        _, cleanup = self._with_home()
        cleanup_stub = self._with_env("CCCC_NOTEBOOKLM_STUB", None)
        try:
            gid = self._create_group("space-degraded")
            bind, _ = self._call(
                "group_space_bind",
                {
                    "group_id": gid,
                    "provider": "notebooklm",
                    "action": "bind",
                    "remote_space_id": "nb_degraded",
                    "by": "user",
                },
            )
            self.assertTrue(bind.ok, getattr(bind, "error", None))

            ingest, _ = self._call(
                "group_space_ingest",
                {
                    "group_id": gid,
                    "provider": "notebooklm",
                    "kind": "context_sync",
                    "payload": {"foo": "bar"},
                    "by": "user",
                },
            )
            self.assertTrue(ingest.ok, getattr(ingest, "error", None))
            job = (ingest.result or {}).get("job") if isinstance(ingest.result, dict) else {}
            self.assertEqual(str(job.get("state") or ""), "failed")
            self.assertEqual(str((job.get("last_error") or {}).get("code") or ""), "space_provider_disabled")

            query, _ = self._call(
                "group_space_query",
                {
                    "group_id": gid,
                    "provider": "notebooklm",
                    "query": "status?",
                },
            )
            self.assertTrue(query.ok, getattr(query, "error", None))
            result = query.result if isinstance(query.result, dict) else {}
            self.assertEqual(bool(result.get("degraded")), True)
            err = result.get("error") if isinstance(result.get("error"), dict) else {}
            self.assertEqual(str(err.get("code") or ""), "space_provider_disabled")
        finally:
            cleanup_stub()
            cleanup()

    def test_jobs_retry_and_cancel(self) -> None:
        _, cleanup = self._with_home()
        cleanup_stub = self._with_env("CCCC_NOTEBOOKLM_STUB", None)
        try:
            gid = self._create_group("space-jobs")
            bind, _ = self._call(
                "group_space_bind",
                {
                    "group_id": gid,
                    "provider": "notebooklm",
                    "action": "bind",
                    "remote_space_id": "nb_jobs",
                    "by": "user",
                },
            )
            self.assertTrue(bind.ok, getattr(bind, "error", None))

            failed_ingest, _ = self._call(
                "group_space_ingest",
                {
                    "group_id": gid,
                    "provider": "notebooklm",
                    "kind": "context_sync",
                    "payload": {"a": 1},
                    "idempotency_key": "retry-1",
                    "by": "user",
                },
            )
            self.assertTrue(failed_ingest.ok, getattr(failed_ingest, "error", None))
            failed_job_id = str((failed_ingest.result or {}).get("job_id") or "")
            self.assertTrue(failed_job_id)

            retried, _ = self._call(
                "group_space_jobs",
                {
                    "group_id": gid,
                    "provider": "notebooklm",
                    "action": "retry",
                    "job_id": failed_job_id,
                    "by": "user",
                },
            )
            self.assertTrue(retried.ok, getattr(retried, "error", None))
            retried_job = (retried.result or {}).get("job") if isinstance(retried.result, dict) else {}
            self.assertEqual(str(retried_job.get("state") or ""), "failed")

            from cccc.daemon.group_space_store import get_space_job

            with patch(
                "cccc.daemon.ops.group_space_ops.execute_space_job",
                side_effect=lambda job_id: get_space_job(job_id),
            ):
                pending_ingest, _ = self._call(
                    "group_space_ingest",
                    {
                        "group_id": gid,
                        "provider": "notebooklm",
                        "kind": "resource_ingest",
                        "payload": {"resource": "doc.md"},
                        "idempotency_key": "cancel-1",
                        "by": "user",
                    },
                )
            self.assertTrue(pending_ingest.ok, getattr(pending_ingest, "error", None))
            pending_job = (pending_ingest.result or {}).get("job") if isinstance(pending_ingest.result, dict) else {}
            self.assertEqual(str(pending_job.get("state") or ""), "pending")
            pending_job_id = str(pending_ingest.result.get("job_id") or "") if isinstance(pending_ingest.result, dict) else ""
            self.assertTrue(pending_job_id)

            canceled, _ = self._call(
                "group_space_jobs",
                {
                    "group_id": gid,
                    "provider": "notebooklm",
                    "action": "cancel",
                    "job_id": pending_job_id,
                    "by": "user",
                },
            )
            self.assertTrue(canceled.ok, getattr(canceled, "error", None))
            canceled_job = (canceled.result or {}).get("job") if isinstance(canceled.result, dict) else {}
            self.assertEqual(str(canceled_job.get("state") or ""), "canceled")
        finally:
            cleanup_stub()
            cleanup()


if __name__ == "__main__":
    unittest.main()
