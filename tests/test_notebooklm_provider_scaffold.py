from __future__ import annotations

import os
import unittest
from unittest.mock import patch


class TestNotebookLMProviderScaffold(unittest.TestCase):
    def test_real_mode_takes_precedence_over_stub_mode(self) -> None:
        from cccc.daemon.group_space_provider import SpaceProviderError, provider_query

        with patch.dict(os.environ, {}, clear=False):
            os.environ["CCCC_NOTEBOOKLM_REAL"] = "1"
            os.environ["CCCC_NOTEBOOKLM_STUB"] = "1"
            os.environ.pop("CCCC_NOTEBOOKLM_AUTH_JSON", None)
            with patch("cccc.daemon.group_space_provider.notebooklm_real_enabled", return_value=True):
                with self.assertRaises(SpaceProviderError) as ctx:
                    provider_query(
                        "notebooklm",
                        remote_space_id="nb_1",
                        query="hello",
                        options={},
                    )
            self.assertEqual(ctx.exception.code, "space_provider_not_configured")
            self.assertTrue(ctx.exception.degrade_provider)

    def test_invalid_auth_json_is_mapped_to_space_provider_error(self) -> None:
        from cccc.daemon.group_space_provider import SpaceProviderError, provider_ingest

        with patch.dict(os.environ, {}, clear=False):
            os.environ["CCCC_NOTEBOOKLM_REAL"] = "1"
            os.environ["CCCC_NOTEBOOKLM_AUTH_JSON"] = "{bad-json"
            with patch("cccc.daemon.group_space_provider.notebooklm_real_enabled", return_value=True):
                with self.assertRaises(SpaceProviderError) as ctx:
                    provider_ingest(
                        "notebooklm",
                        remote_space_id="nb_2",
                        kind="context_sync",
                        payload={"k": "v"},
                    )
            self.assertEqual(ctx.exception.code, "space_provider_auth_invalid")
            self.assertTrue(ctx.exception.degrade_provider)
            self.assertFalse(ctx.exception.transient)

    def test_compat_mismatch_is_mapped_to_space_provider_error(self) -> None:
        from cccc.daemon.group_space_provider import SpaceProviderError, provider_query
        from cccc.providers.notebooklm.compat import NotebookLMCompatStatus

        with patch.dict(os.environ, {}, clear=False):
            os.environ["CCCC_NOTEBOOKLM_REAL"] = "1"
            os.environ["CCCC_NOTEBOOKLM_AUTH_JSON"] = (
                '{"cookies":[{"name":"__Secure-1PSID","value":"x","domain":".google.com"}]}'
            )
            with patch("cccc.daemon.group_space_provider.notebooklm_real_enabled", return_value=True):
                with patch(
                    "cccc.providers.notebooklm.health.probe_notebooklm_vendor",
                    return_value=NotebookLMCompatStatus(compatible=False, reason="forced mismatch"),
                ):
                    with self.assertRaises(SpaceProviderError) as ctx:
                        provider_query(
                            "notebooklm",
                            remote_space_id="nb_3",
                            query="status",
                            options={},
                        )
            self.assertEqual(ctx.exception.code, "space_provider_compat_mismatch")
            self.assertTrue(ctx.exception.degrade_provider)

    def test_real_adapter_maps_unexpected_runtime_error(self) -> None:
        from cccc.daemon.group_space_provider import SpaceProviderError, provider_query
        from cccc.providers.notebooklm.compat import NotebookLMCompatStatus

        def _boom(coro):
            coro.close()
            raise RuntimeError("boom")

        with patch.dict(os.environ, {}, clear=False):
            os.environ["CCCC_NOTEBOOKLM_REAL"] = "1"
            os.environ["CCCC_NOTEBOOKLM_AUTH_JSON"] = (
                '{"cookies":[{"name":"__Secure-1PSID","value":"x","domain":".google.com"}]}'
            )
            with patch("cccc.daemon.group_space_provider.notebooklm_real_enabled", return_value=True):
                with patch(
                    "cccc.providers.notebooklm.health.probe_notebooklm_vendor",
                    return_value=NotebookLMCompatStatus(compatible=True, reason="ok"),
                ):
                    with patch(
                        "cccc.providers.notebooklm.adapter._run_coroutine_sync",
                        side_effect=_boom,
                    ):
                        with self.assertRaises(SpaceProviderError) as ctx:
                            provider_query(
                                "notebooklm",
                                remote_space_id="nb_4",
                                query="status",
                                options={},
                            )
            self.assertEqual(ctx.exception.code, "space_provider_upstream_error")
            self.assertTrue(ctx.exception.transient)
            self.assertFalse(ctx.exception.degrade_provider)

    def test_real_adapter_query_success_path_via_runner_mock(self) -> None:
        from cccc.daemon.group_space_provider import provider_query
        from cccc.providers.notebooklm.compat import NotebookLMCompatStatus

        def _ok(coro):
            coro.close()
            return {"answer": "ok", "references": [{"source_id": "s1"}]}

        with patch.dict(os.environ, {}, clear=False):
            os.environ["CCCC_NOTEBOOKLM_REAL"] = "1"
            os.environ["CCCC_NOTEBOOKLM_AUTH_JSON"] = (
                '{"cookies":[{"name":"__Secure-1PSID","value":"x","domain":".google.com"}]}'
            )
            with patch("cccc.daemon.group_space_provider.notebooklm_real_enabled", return_value=True):
                with patch(
                    "cccc.providers.notebooklm.health.probe_notebooklm_vendor",
                    return_value=NotebookLMCompatStatus(compatible=True, reason="ok"),
                ):
                    with patch(
                        "cccc.providers.notebooklm.adapter._run_coroutine_sync",
                        side_effect=_ok,
                    ):
                        out = provider_query(
                            "notebooklm",
                            remote_space_id="nb_4",
                            query="status",
                            options={},
                        )
        self.assertEqual(str(out.get("answer") or ""), "ok")
        refs = out.get("references") if isinstance(out.get("references"), list) else []
        self.assertEqual(len(refs), 1)

    def test_notebooklm_error_flags_are_preserved_by_provider_mapping(self) -> None:
        from cccc.daemon.group_space_provider import SpaceProviderError, provider_ingest
        from cccc.providers.notebooklm.errors import NotebookLMProviderError

        class _DummyAdapter:
            def ingest(self, *, remote_space_id: str, kind: str, payload: dict):
                _ = remote_space_id, kind, payload
                raise NotebookLMProviderError(
                    code="space_upstream_busy",
                    message="upstream busy",
                    transient=True,
                    degrade_provider=False,
                )

        with patch("cccc.daemon.group_space_provider.notebooklm_real_enabled", return_value=True):
            with patch("cccc.daemon.group_space_provider.get_notebooklm_adapter", return_value=_DummyAdapter()):
                with self.assertRaises(SpaceProviderError) as ctx:
                    provider_ingest(
                        "notebooklm",
                        remote_space_id="nb_5",
                        kind="context_sync",
                        payload={"k": "v"},
                    )
        self.assertEqual(ctx.exception.code, "space_upstream_busy")
        self.assertTrue(ctx.exception.transient)
        self.assertFalse(ctx.exception.degrade_provider)


if __name__ == "__main__":
    unittest.main()
