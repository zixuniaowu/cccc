from __future__ import annotations

import os
import tempfile
import unittest

from fastapi.testclient import TestClient
from unittest.mock import patch


class TestWebGroupSpaceApi(unittest.TestCase):
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

    def _create_group(self, title: str = "web-space") -> str:
        from cccc.kernel.group import create_group
        from cccc.kernel.registry import load_registry

        reg = load_registry()
        group = create_group(reg, title=title, topic="")
        return group.group_id

    def _local_call_daemon(self, req: dict):
        from cccc.contracts.v1 import DaemonRequest
        from cccc.daemon.server import handle_request

        request = DaemonRequest.model_validate(req)
        resp, _ = handle_request(request)
        return resp.model_dump(exclude_none=True)

    def test_group_space_endpoints_work_in_normal_mode(self) -> None:
        from cccc.ports.web.app import create_app

        _, cleanup = self._with_home()
        cleanup_stub = self._with_env("CCCC_NOTEBOOKLM_STUB", "1")
        try:
            gid = self._create_group("web-space-normal")
            with patch("cccc.ports.web.app.call_daemon", side_effect=self._local_call_daemon):
                client = TestClient(create_app())

                status = client.get(f"/api/v1/groups/{gid}/space/status")
                self.assertEqual(status.status_code, 200)
                status_body = status.json()
                self.assertTrue(status_body.get("ok"))

                bind = client.post(
                    f"/api/v1/groups/{gid}/space/bind",
                    json={
                        "by": "user",
                        "provider": "notebooklm",
                        "action": "bind",
                        "remote_space_id": "nb_web_1",
                    },
                )
                self.assertEqual(bind.status_code, 200)
                bind_body = bind.json()
                self.assertTrue(bind_body.get("ok"))
                binding = ((bind_body.get("result") or {}).get("binding") or {})
                self.assertEqual(str(binding.get("status") or ""), "bound")

                query = client.post(
                    f"/api/v1/groups/{gid}/space/query",
                    json={"provider": "notebooklm", "query": "status?", "options": {}},
                )
                self.assertEqual(query.status_code, 200)
                query_body = query.json()
                self.assertTrue(query_body.get("ok"))

                ingest = client.post(
                    f"/api/v1/groups/{gid}/space/ingest",
                    json={
                        "by": "user",
                        "provider": "notebooklm",
                        "kind": "context_sync",
                        "payload": {"vision": "v0.5"},
                        "idempotency_key": "web-space-1",
                    },
                )
                self.assertEqual(ingest.status_code, 200)
                ingest_body = ingest.json()
                self.assertTrue(ingest_body.get("ok"))

                jobs = client.get(f"/api/v1/groups/{gid}/space/jobs?provider=notebooklm&limit=20")
                self.assertEqual(jobs.status_code, 200)
                jobs_body = jobs.json()
                self.assertTrue(jobs_body.get("ok"))
                jobs_list = ((jobs_body.get("result") or {}).get("jobs") or [])
                self.assertIsInstance(jobs_list, list)
        finally:
            cleanup_stub()
            cleanup()

    def test_group_space_write_endpoints_blocked_in_exhibit_mode(self) -> None:
        from cccc.ports.web.app import create_app

        _, cleanup = self._with_home()
        cleanup_mode = self._with_env("CCCC_WEB_MODE", "exhibit")
        try:
            gid = self._create_group("web-space-exhibit")
            with patch("cccc.ports.web.app.call_daemon", side_effect=self._local_call_daemon):
                client = TestClient(create_app())

                write_resp = client.post(
                    f"/api/v1/groups/{gid}/space/bind",
                    json={
                        "by": "user",
                        "provider": "notebooklm",
                        "action": "bind",
                        "remote_space_id": "nb_exhibit",
                    },
                )
                self.assertEqual(write_resp.status_code, 403)
                body = write_resp.json()
                self.assertFalse(body.get("ok"))
                self.assertEqual(str((body.get("error") or {}).get("code") or ""), "read_only")
        finally:
            cleanup_mode()
            cleanup()


if __name__ == "__main__":
    unittest.main()
