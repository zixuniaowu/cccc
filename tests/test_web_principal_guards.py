import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


class TestWebPrincipalGuards(unittest.TestCase):
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

    def _local_call_daemon(self, req: dict):
        from cccc.contracts.v1 import DaemonRequest
        from cccc.daemon.server import handle_request

        request = DaemonRequest.model_validate(req)
        resp, _ = handle_request(request)
        return resp.model_dump(exclude_none=True)

    def _create_group(self, title: str) -> str:
        from cccc.kernel.group import create_group
        from cccc.kernel.registry import load_registry

        reg = load_registry()
        group = create_group(reg, title=title, topic="")
        return group.group_id

    def _create_client(self) -> TestClient:
        from cccc.ports.web.app import create_app

        return TestClient(create_app())

    def test_invalid_access_token_gets_401_on_group_route(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group("g1")
            client = self._create_client()
            resp = client.get(f"/api/v1/groups/{gid}", headers={"Authorization": "Bearer invalid-token"})
            self.assertEqual(resp.status_code, 401)
            self.assertEqual(str((resp.json().get("error") or {}).get("code") or ""), "unauthorized")
        finally:
            cleanup()

    def test_anonymous_forbidden_when_access_tokens_exist(self) -> None:
        from cccc.kernel.access_tokens import create_access_token

        _, cleanup = self._with_home()
        try:
            gid = self._create_group("g1")
            create_access_token("user-a", allowed_groups=[gid], is_admin=False)
            client = self._create_client()
            resp = client.get(f"/api/v1/groups/{gid}")
            self.assertEqual(resp.status_code, 401)
            self.assertEqual(str((resp.json().get("error") or {}).get("code") or ""), "unauthorized")
        finally:
            cleanup()

    def test_groups_list_filters_by_allowed_groups(self) -> None:
        from cccc.kernel.access_tokens import create_access_token

        _, cleanup = self._with_home()
        try:
            gid1 = self._create_group("g1")
            self._create_group("g2")
            token = str(create_access_token("user-a", allowed_groups=[gid1], is_admin=False).get("token") or "")
            with patch("cccc.ports.web.app.call_daemon", side_effect=self._local_call_daemon):
                client = self._create_client()
                resp = client.get("/api/v1/groups", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(resp.status_code, 200)
            groups = ((resp.json().get("result") or {}).get("groups") or [])
            ids = sorted(str(item.get("group_id") or item.get("id") or "") for item in groups if isinstance(item, dict))
            self.assertEqual(ids, [gid1])
        finally:
            cleanup()

    def test_non_admin_cannot_access_global_control_plane(self) -> None:
        from cccc.kernel.access_tokens import create_access_token

        _, cleanup = self._with_home()
        try:
            token = str(create_access_token("user-a", allowed_groups=[], is_admin=False).get("token") or "")
            client = self._create_client()
            resp = client.get("/api/v1/observability", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(resp.status_code, 403)
            self.assertEqual(str((resp.json().get("error") or {}).get("code") or ""), "permission_denied")
        finally:
            cleanup()

    def test_websocket_terminal_checks_group_access(self) -> None:
        from cccc.kernel.access_tokens import create_access_token

        _, cleanup = self._with_home()
        try:
            gid1 = self._create_group("g1")
            gid2 = self._create_group("g2")
            token = str(create_access_token("user-a", allowed_groups=[gid1], is_admin=False).get("token") or "")
            client = self._create_client()
            with client.websocket_connect(f"/api/v1/groups/{gid2}/actors/demo/term?token={token}") as ws:
                payload = ws.receive_json()
                self.assertFalse(payload.get("ok"))
                self.assertEqual(str((payload.get("error") or {}).get("code") or ""), "permission_denied")
        finally:
            cleanup()

    def test_websocket_auth_accepts_valid_access_token(self) -> None:
        from cccc.kernel.access_tokens import create_access_token

        _, cleanup = self._with_home()
        try:
            gid = self._create_group("g1")
            token = str(create_access_token("user-a", allowed_groups=[gid], is_admin=True).get("token") or "")
            client = self._create_client()
            with client.websocket_connect(f"/api/v1/groups/{gid}/actors/demo/term?token={token}") as ws:
                payload = ws.receive_json()
                self.assertFalse(payload.get("ok"))
                self.assertNotEqual(str((payload.get("error") or {}).get("code") or ""), "auth_required")
        finally:
            cleanup()


if __name__ == "__main__":
    unittest.main()
