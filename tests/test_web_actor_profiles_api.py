import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


class TestWebActorProfilesApi(unittest.TestCase):
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

    def _client(self) -> TestClient:
        from cccc.ports.web.app import create_app

        return TestClient(create_app())

    def _local_call_daemon(self, req: dict):
        from cccc.contracts.v1 import DaemonRequest
        from cccc.daemon.server import handle_request

        request = DaemonRequest.model_validate(req)
        resp, _ = handle_request(request)
        return resp.model_dump(exclude_none=True)

    def test_profiles_routes_support_my_and_all_views(self) -> None:
        from cccc.kernel.access_tokens import create_access_token

        _, cleanup = self._with_home()
        try:
            admin = str(create_access_token("admin-user", is_admin=True).get("token") or "")
            member = str(create_access_token("member-user", is_admin=False).get("token") or "")
            with patch("cccc.ports.web.app.call_daemon", side_effect=self._local_call_daemon):
                client = self._client()

                create_my = client.put(
                    "/api/v1/profiles/member-profile",
                    headers={"Authorization": f"Bearer {member}"},
                    json={
                        "scope": "user",
                        "owner_id": "member-user",
                        "name": "Member Profile",
                        "runtime": "codex",
                        "runner": "pty",
                        "command": [],
                    },
                )
                self.assertEqual(create_my.status_code, 200)
                create_my_body = create_my.json()
                self.assertTrue(bool(create_my_body.get("ok")))

                create_global = client.post(
                    "/api/v1/actor_profiles",
                    headers={"Authorization": f"Bearer {admin}"},
                    json={
                        "by": "user",
                        "profile": {
                            "id": "global-profile",
                            "name": "Global Profile",
                            "runtime": "codex",
                            "runner": "pty",
                            "command": [],
                        },
                    },
                )
                self.assertEqual(create_global.status_code, 200)
                self.assertTrue(bool(create_global.json().get("ok")))

                my_resp = client.get("/api/v1/profiles?view=my", headers={"Authorization": f"Bearer {member}"})
                self.assertEqual(my_resp.status_code, 200)
                my_profiles = (((my_resp.json().get("result") or {}).get("profiles")) or [])
                self.assertEqual([(item.get("id"), item.get("scope"), item.get("owner_id")) for item in my_profiles], [("member-profile", "user", "member-user")])

                denied_all = client.get("/api/v1/profiles?view=all", headers={"Authorization": f"Bearer {member}"})
                self.assertEqual(denied_all.status_code, 200)
                denied_all_body = denied_all.json()
                self.assertFalse(bool(denied_all_body.get("ok")))
                self.assertEqual(str(((denied_all_body.get("error") or {}).get("code")) or ""), "permission_denied")

                all_resp = client.get("/api/v1/profiles?view=all", headers={"Authorization": f"Bearer {admin}"})
                self.assertEqual(all_resp.status_code, 200)
                all_profiles = (((all_resp.json().get("result") or {}).get("profiles")) or [])
                self.assertEqual(
                    sorted((item.get("id"), item.get("scope"), item.get("owner_id")) for item in all_profiles),
                    [("global-profile", "global", ""), ("member-profile", "user", "member-user")],
                )
        finally:
            cleanup()

    def test_legacy_actor_profiles_routes_remain_global_compatible(self) -> None:
        from cccc.kernel.access_tokens import create_access_token

        _, cleanup = self._with_home()
        try:
            admin = str(create_access_token("admin-user", is_admin=True).get("token") or "")
            member = str(create_access_token("member-user", is_admin=False).get("token") or "")
            with patch("cccc.ports.web.app.call_daemon", side_effect=self._local_call_daemon):
                client = self._client()

                create_global = client.post(
                    "/api/v1/actor_profiles",
                    headers={"Authorization": f"Bearer {admin}"},
                    json={
                        "by": "user",
                        "profile": {
                            "id": "legacy-global",
                            "name": "Legacy Global",
                            "runtime": "codex",
                            "runner": "pty",
                            "command": [],
                        },
                    },
                )
                self.assertEqual(create_global.status_code, 200)
                self.assertTrue(bool(create_global.json().get("ok")))

                list_global = client.get("/api/v1/actor_profiles", headers={"Authorization": f"Bearer {member}"})
                self.assertEqual(list_global.status_code, 200)
                profiles = (((list_global.json().get("result") or {}).get("profiles")) or [])
                self.assertEqual([(item.get("id"), item.get("scope"), item.get("owner_id")) for item in profiles], [("legacy-global", "global", "")])

                delete_global = client.delete(
                    "/api/v1/actor_profiles/legacy-global?by=user",
                    headers={"Authorization": f"Bearer {admin}"},
                )
                self.assertEqual(delete_global.status_code, 200)
                self.assertTrue(bool(delete_global.json().get("ok")))
        finally:
            cleanup()

    def test_member_can_manage_own_profile_secrets_via_profiles_routes(self) -> None:
        from cccc.kernel.access_tokens import create_access_token

        _, cleanup = self._with_home()
        try:
            member = str(create_access_token("member-user", is_admin=False).get("token") or "")
            other = str(create_access_token("other-user", is_admin=False).get("token") or "")
            with patch("cccc.ports.web.app.call_daemon", side_effect=self._local_call_daemon):
                client = self._client()

                create_my = client.put(
                    "/api/v1/profiles/member-secret-profile",
                    headers={"Authorization": f"Bearer {member}"},
                    json={
                        "scope": "user",
                        "owner_id": "member-user",
                        "name": "Member Secret Profile",
                        "runtime": "codex",
                        "runner": "pty",
                        "command": [],
                    },
                )
                self.assertEqual(create_my.status_code, 200)
                self.assertTrue(bool(create_my.json().get("ok")))

                update_secret = client.post(
                    "/api/v1/profiles/member-secret-profile/env_private",
                    headers={"Authorization": f"Bearer {member}"},
                    json={
                        "by": "user",
                        "scope": "user",
                        "owner_id": "member-user",
                        "set": {"API_KEY": "member-secret"},
                        "unset": [],
                        "clear": False,
                    },
                )
                self.assertEqual(update_secret.status_code, 200)
                self.assertTrue(bool(update_secret.json().get("ok")))

                list_secret = client.get(
                    "/api/v1/profiles/member-secret-profile/env_private?by=user&scope=user&owner_id=member-user",
                    headers={"Authorization": f"Bearer {member}"},
                )
                self.assertEqual(list_secret.status_code, 200)
                self.assertEqual((((list_secret.json().get("result") or {}).get("keys")) or []), ["API_KEY"])

                denied = client.get(
                    "/api/v1/profiles/member-secret-profile/env_private?by=user&scope=user&owner_id=member-user",
                    headers={"Authorization": f"Bearer {other}"},
                )
                self.assertEqual(denied.status_code, 200)
                self.assertFalse(bool(denied.json().get("ok")))
                self.assertEqual(str(((denied.json().get("error") or {}).get("code")) or ""), "permission_denied")
        finally:
            cleanup()
