import os
import tempfile
import unittest
from dataclasses import asdict

from fastapi.testclient import TestClient


class TestWebAccessAuth(unittest.TestCase):
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

    def _create_probe_client(self) -> TestClient:
        from fastapi import Request
        from cccc.ports.web.app import create_app

        app = create_app()

        @app.get("/__test__/principal")
        async def principal_probe(request: Request) -> dict:
            principal = getattr(request.state, "principal", None)
            if principal is None:
                return {"present": False}
            payload = asdict(principal)
            payload["allowed_groups"] = list(principal.allowed_groups)
            payload["present"] = True
            return payload

        return TestClient(app)

    def test_web_access_session_reports_open_access_before_tokens_exist(self) -> None:
        _, cleanup = self._with_home()
        try:
            client = self._create_probe_client()
            resp = client.get("/api/v1/web_access/session")
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            session = ((body.get("result") or {}).get("web_access_session") or {})
            self.assertEqual(bool(session.get("login_active")), False)
            self.assertEqual(bool(session.get("current_browser_signed_in")), False)
        finally:
            cleanup()

    def test_web_access_session_reports_signed_in_browser(self) -> None:
        from cccc.kernel.access_tokens import create_access_token

        _, cleanup = self._with_home()
        try:
            created = create_access_token("admin-user", allowed_groups=["g-1"], is_admin=True)
            token = str(created.get("token") or "")
            client = self._create_probe_client()
            resp = client.get("/api/v1/web_access/session", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            session = ((body.get("result") or {}).get("web_access_session") or {})
            self.assertEqual(bool(session.get("login_active")), True)
            self.assertEqual(bool(session.get("current_browser_signed_in")), True)
            self.assertEqual(str(session.get("user_id") or ""), "admin-user")
            self.assertEqual(bool(session.get("is_admin")), True)
            self.assertEqual(session.get("allowed_groups"), ["g-1"])
        finally:
            cleanup()

    def test_request_without_access_token_gets_anonymous_principal(self) -> None:
        _, cleanup = self._with_home()
        try:
            client = self._create_probe_client()
            resp = client.get("/__test__/principal")
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertTrue(body.get("present"))
            self.assertEqual(str(body.get("kind") or ""), "anonymous")
        finally:
            cleanup()

    def test_valid_access_token_resolves_principal_and_sets_cookie(self) -> None:
        from cccc.kernel.access_tokens import create_access_token

        _, cleanup = self._with_home()
        try:
            created = create_access_token("user-a", allowed_groups=["g-1"], is_admin=False)
            token = str(created.get("token") or "")
            client = self._create_probe_client()
            resp = client.get("/__test__/principal", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertEqual(str(body.get("kind") or ""), "user")
            self.assertEqual(str(body.get("user_id") or ""), "user-a")
            self.assertEqual(body.get("allowed_groups"), ["g-1"])
            self.assertFalse(bool(body.get("is_admin")))
            self.assertIn("cccc_access_token=", str(resp.headers.get("set-cookie") or ""))
        finally:
            cleanup()

    def test_stale_cookie_is_ignored_when_no_access_token_is_configured(self) -> None:
        _, cleanup = self._with_home()
        try:
            client = self._create_probe_client()
            client.cookies.set("cccc_access_token", "stale-cookie")
            resp = client.get("/__test__/principal")
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertEqual(str(body.get("kind") or ""), "anonymous")
            self.assertIn("cccc_access_token=\"\"", str(resp.headers.get("set-cookie") or ""))
        finally:
            cleanup()
