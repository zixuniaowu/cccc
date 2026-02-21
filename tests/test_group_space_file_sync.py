import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestGroupSpaceFileSync(unittest.TestCase):
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

    def _call(self, op: str, args: dict):
        from cccc.contracts.v1 import DaemonRequest
        from cccc.daemon.server import handle_request

        return handle_request(DaemonRequest.model_validate({"op": op, "args": args}))

    def _create_group(self, title: str = "space-sync") -> str:
        resp, _ = self._call("group_create", {"title": title, "topic": "", "by": "user"})
        self.assertTrue(resp.ok, getattr(resp, "error", None))
        gid = str((resp.result or {}).get("group_id") or "").strip()
        self.assertTrue(gid)
        return gid

    def _attach(self, group_id: str, project_dir: Path) -> None:
        resp, _ = self._call("attach", {"path": str(project_dir), "group_id": group_id, "by": "user"})
        self.assertTrue(resp.ok, getattr(resp, "error", None))

    def _bind(self, group_id: str, remote_space_id: str = "nb_sync_test") -> None:
        resp, _ = self._call(
            "group_space_bind",
            {
                "group_id": group_id,
                "provider": "notebooklm",
                "action": "bind",
                "remote_space_id": remote_space_id,
                "by": "user",
            },
        )
        self.assertTrue(resp.ok, getattr(resp, "error", None))

    def test_sync_uploads_local_space_files_and_writes_index(self) -> None:
        _, cleanup = self._with_home()
        project_ctx = tempfile.TemporaryDirectory()
        project_dir = Path(project_ctx.__enter__()).resolve()
        try:
            gid = self._create_group("space-sync-upload")
            self._attach(gid, project_dir)
            self._bind(gid, "nb_sync_upload")

            space_dir = project_dir / "space"
            space_dir.mkdir(parents=True, exist_ok=True)
            (space_dir / "a.txt").write_text("alpha", encoding="utf-8")
            (space_dir / "b.txt").write_text("beta", encoding="utf-8")

            remote: dict[str, dict] = {}
            counter = {"n": 0}

            def _list_sources(provider: str, *, remote_space_id: str):
                _ = provider, remote_space_id
                return {"sources": list(remote.values())}

            def _add_file(provider: str, *, remote_space_id: str, file_path: str):
                _ = provider, remote_space_id
                counter["n"] += 1
                sid = f"src_{counter['n']}"
                remote[sid] = {"source_id": sid, "title": Path(file_path).name}
                return {"source_id": sid, "title": Path(file_path).name}

            def _rename(provider: str, *, remote_space_id: str, source_id: str, new_title: str):
                _ = provider, remote_space_id
                item = remote.get(source_id) or {"source_id": source_id}
                item["title"] = new_title
                remote[source_id] = item
                return dict(item)

            def _delete(provider: str, *, remote_space_id: str, source_id: str):
                _ = provider, remote_space_id
                remote.pop(source_id, None)
                return {"deleted": True}

            from cccc.daemon.group_space_sync import sync_group_space_files

            with patch("cccc.daemon.group_space_sync.provider_list_sources", side_effect=_list_sources), \
                 patch("cccc.daemon.group_space_sync.provider_add_file_source", side_effect=_add_file), \
                 patch("cccc.daemon.group_space_sync.provider_rename_source", side_effect=_rename), \
                 patch("cccc.daemon.group_space_sync.provider_delete_source", side_effect=_delete):
                result = sync_group_space_files(gid, provider="notebooklm", force=True)

            self.assertTrue(bool(result.get("ok")))
            self.assertEqual(bool(result.get("converged")), True)
            self.assertEqual(int(result.get("uploaded") or 0), 2)
            self.assertEqual(int(result.get("unsynced_count") or 0), 0)

            index_path = space_dir / ".space-index.json"
            self.assertTrue(index_path.exists())
            doc = json.loads(index_path.read_text(encoding="utf-8"))
            entries = doc.get("entries") if isinstance(doc.get("entries"), dict) else {}
            self.assertEqual(set(entries.keys()), {"a.txt", "b.txt"})

            with patch("cccc.daemon.group_space_sync.provider_list_sources", side_effect=_list_sources), \
                 patch("cccc.daemon.group_space_sync.provider_add_file_source", side_effect=_add_file), \
                 patch("cccc.daemon.group_space_sync.provider_rename_source", side_effect=_rename), \
                 patch("cccc.daemon.group_space_sync.provider_delete_source", side_effect=_delete):
                second = sync_group_space_files(gid, provider="notebooklm", force=False)
            self.assertTrue(bool(second.get("ok")))
            self.assertEqual(bool(second.get("skipped")), True)
        finally:
            project_ctx.__exit__(None, None, None)
            cleanup()

    def test_sync_deletes_remote_for_local_removed_file(self) -> None:
        _, cleanup = self._with_home()
        project_ctx = tempfile.TemporaryDirectory()
        project_dir = Path(project_ctx.__enter__()).resolve()
        try:
            gid = self._create_group("space-sync-delete")
            self._attach(gid, project_dir)
            self._bind(gid, "nb_sync_delete")

            space_dir = project_dir / "space"
            space_dir.mkdir(parents=True, exist_ok=True)
            target = space_dir / "remove_me.txt"
            target.write_text("to-delete", encoding="utf-8")

            remote: dict[str, dict] = {}
            counter = {"n": 0}

            def _list_sources(provider: str, *, remote_space_id: str):
                _ = provider, remote_space_id
                return {"sources": list(remote.values())}

            def _add_file(provider: str, *, remote_space_id: str, file_path: str):
                _ = provider, remote_space_id
                counter["n"] += 1
                sid = f"src_{counter['n']}"
                remote[sid] = {"source_id": sid, "title": Path(file_path).name}
                return {"source_id": sid, "title": Path(file_path).name}

            def _rename(provider: str, *, remote_space_id: str, source_id: str, new_title: str):
                _ = provider, remote_space_id
                item = remote.get(source_id) or {"source_id": source_id}
                item["title"] = new_title
                remote[source_id] = item
                return dict(item)

            def _delete(provider: str, *, remote_space_id: str, source_id: str):
                _ = provider, remote_space_id
                remote.pop(source_id, None)
                return {"deleted": True}

            from cccc.daemon.group_space_sync import sync_group_space_files

            with patch("cccc.daemon.group_space_sync.provider_list_sources", side_effect=_list_sources), \
                 patch("cccc.daemon.group_space_sync.provider_add_file_source", side_effect=_add_file), \
                 patch("cccc.daemon.group_space_sync.provider_rename_source", side_effect=_rename), \
                 patch("cccc.daemon.group_space_sync.provider_delete_source", side_effect=_delete):
                first = sync_group_space_files(gid, provider="notebooklm", force=True)
            self.assertTrue(bool(first.get("ok")))
            self.assertEqual(len(remote), 1)

            target.unlink(missing_ok=True)

            with patch("cccc.daemon.group_space_sync.provider_list_sources", side_effect=_list_sources), \
                 patch("cccc.daemon.group_space_sync.provider_add_file_source", side_effect=_add_file), \
                 patch("cccc.daemon.group_space_sync.provider_rename_source", side_effect=_rename), \
                 patch("cccc.daemon.group_space_sync.provider_delete_source", side_effect=_delete):
                second = sync_group_space_files(gid, provider="notebooklm", force=True)
            self.assertTrue(bool(second.get("ok")))
            self.assertEqual(bool(second.get("converged")), True)
            self.assertGreaterEqual(int(second.get("deleted") or 0), 1)
            self.assertEqual(len(remote), 0)
        finally:
            project_ctx.__exit__(None, None, None)
            cleanup()


if __name__ == "__main__":
    unittest.main()
