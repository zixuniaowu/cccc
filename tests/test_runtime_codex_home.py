from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import Callable

from cccc.kernel.runtime import inject_runtime_home_env


class TestRuntimeCodexHome(unittest.TestCase):
    def _with_home(self) -> tuple[Path, Callable[[], None]]:
        old_home = os.environ.get("CCCC_HOME")
        td_ctx = tempfile.TemporaryDirectory()
        td = Path(td_ctx.__enter__())
        os.environ["CCCC_HOME"] = str(td)

        def cleanup() -> None:
            td_ctx.__exit__(None, None, None)
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home

        return td, cleanup

    def test_inject_runtime_home_env_sets_actor_scoped_codex_home(self) -> None:
        home, cleanup = self._with_home()
        try:
            env = inject_runtime_home_env({"OPENAI_API_KEY": "sk-test"}, runtime="codex", group_id="g_demo", actor_id="peer1")
            expected = (home / "groups" / "g_demo" / "runtime" / "codex" / "peer1").resolve()
            self.assertEqual(Path(str(env.get("CODEX_HOME") or "")).resolve(), expected)
            self.assertTrue(expected.exists())
            self.assertTrue(expected.is_dir())
        finally:
            cleanup()

    def test_inject_runtime_home_env_skips_default_isolation_without_auth_env(self) -> None:
        _, cleanup = self._with_home()
        try:
            env = inject_runtime_home_env({}, runtime="codex", group_id="g_demo", actor_id="peer1")
            self.assertNotIn("CODEX_HOME", env)
        finally:
            cleanup()

    def test_inject_runtime_home_env_ignores_process_auth_env_when_actor_env_is_empty(self) -> None:
        _, cleanup = self._with_home()
        old_key = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "sk-process-only"
        try:
            env = inject_runtime_home_env({}, runtime="codex", group_id="g_demo", actor_id="peer1")
            self.assertNotIn("CODEX_HOME", env)
        finally:
            if old_key is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = old_key
            cleanup()

    def test_inject_runtime_home_env_does_not_isolate_for_base_url_only(self) -> None:
        _, cleanup = self._with_home()
        try:
            env = inject_runtime_home_env(
                {"OPENAI_BASE_URL": "https://api.example.com"},
                runtime="codex",
                group_id="g_demo",
                actor_id="peer1",
            )
            self.assertNotIn("CODEX_HOME", env)
        finally:
            cleanup()

    def test_inject_runtime_home_env_keeps_explicit_codex_home(self) -> None:
        home, cleanup = self._with_home()
        try:
            explicit = str(home / "custom-codex-home")
            env = inject_runtime_home_env(
                {"CODEX_HOME": explicit, "OPENAI_API_KEY": "sk-test"},
                runtime="codex",
                group_id="g_demo",
                actor_id="peer1",
            )
            self.assertEqual(str(env.get("CODEX_HOME") or ""), explicit)
        finally:
            cleanup()
