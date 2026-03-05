"""
Context v2 专项测试: CAS, permissions, cycle detection, panorama projection, task tree ops.
"""

import os
import tempfile
import unittest


class TestContextV2Ops(unittest.TestCase):
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

    def _create_group(self, title: str = "v2-test") -> str:
        resp, _ = self._call("group_create", {"title": title, "topic": "", "by": "user"})
        self.assertTrue(resp.ok, getattr(resp, "error", None))
        gid = str((resp.result or {}).get("group_id") or "").strip()
        self.assertTrue(gid)
        return gid

    # =========================================================================
    # CAS (if_version)
    # =========================================================================

    def test_cas_version_conflict_rejects_batch(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()

            # Get current version
            get_resp, _ = self._call("context_get", {"group_id": gid})
            self.assertTrue(get_resp.ok)
            version = (get_resp.result or {}).get("version")
            self.assertTrue(version)

            # Sync with correct version succeeds
            ok_resp, _ = self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "vision.update", "vision": "test"}],
                "if_version": version,
            })
            self.assertTrue(ok_resp.ok, getattr(ok_resp, "error", None))

            # Sync with stale version fails
            fail_resp, _ = self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "vision.update", "vision": "test2"}],
                "if_version": version,  # stale now
            })
            self.assertFalse(fail_resp.ok)
            self.assertEqual(fail_resp.error.code, "version_conflict")
        finally:
            cleanup()

    def test_cas_omitted_always_succeeds(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            for i in range(3):
                resp, _ = self._call("context_sync", {
                    "group_id": gid, "by": "user",
                    "ops": [{"op": "vision.update", "vision": f"v{i}"}],
                })
                self.assertTrue(resp.ok, getattr(resp, "error", None))
        finally:
            cleanup()

    # =========================================================================
    # Task tree: parent_id, move, cycle detection
    # =========================================================================

    def test_task_create_with_parent_id(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()

            # Create root task
            resp, _ = self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "task.create", "name": "Phase 1", "goal": "root"}],
            })
            self.assertTrue(resp.ok)

            # Find root task ID
            list_resp, _ = self._call("task_list", {"group_id": gid})
            root_id = list_resp.result["tasks"][0]["id"]
            self.assertIsNone(list_resp.result["tasks"][0]["parent_id"])

            # Create child task
            resp2, _ = self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "task.create", "name": "Subtask", "goal": "child", "parent_id": root_id}],
            })
            self.assertTrue(resp2.ok)

            # Verify child
            list_resp2, _ = self._call("task_list", {"group_id": gid})
            tasks = list_resp2.result["tasks"]
            child = [t for t in tasks if t["parent_id"] == root_id]
            self.assertEqual(len(child), 1)
            self.assertEqual(child[0]["name"], "Subtask")
        finally:
            cleanup()

    def test_task_create_with_nonexistent_parent_fails(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            resp, _ = self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "task.create", "name": "orphan", "goal": "x", "parent_id": "T999"}],
            })
            self.assertFalse(resp.ok)
            self.assertIn("not found", str(resp.error.message).lower())
        finally:
            cleanup()

    def test_task_move_basic(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()

            # Create two root tasks
            self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [
                    {"op": "task.create", "name": "A", "goal": "a"},
                    {"op": "task.create", "name": "B", "goal": "b"},
                ],
            })
            list_resp, _ = self._call("task_list", {"group_id": gid})
            tasks = list_resp.result["tasks"]
            a_id = tasks[0]["id"]
            b_id = tasks[1]["id"]

            # Move B under A
            move_resp, _ = self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "task.move", "task_id": b_id, "new_parent_id": a_id}],
            })
            self.assertTrue(move_resp.ok, getattr(move_resp, "error", None))

            # Verify
            list_resp2, _ = self._call("task_list", {"group_id": gid})
            b_task = [t for t in list_resp2.result["tasks"] if t["id"] == b_id][0]
            self.assertEqual(b_task["parent_id"], a_id)
        finally:
            cleanup()

    def test_task_move_cycle_detection(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()

            # Create A -> B -> C chain
            self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "task.create", "name": "A", "goal": "a"}],
            })
            a_id = self._call("task_list", {"group_id": gid})[0].result["tasks"][0]["id"]

            self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "task.create", "name": "B", "goal": "b", "parent_id": a_id}],
            })
            b_id = [t for t in self._call("task_list", {"group_id": gid})[0].result["tasks"] if t["name"] == "B"][0]["id"]

            self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "task.create", "name": "C", "goal": "c", "parent_id": b_id}],
            })
            c_id = [t for t in self._call("task_list", {"group_id": gid})[0].result["tasks"] if t["name"] == "C"][0]["id"]

            # Try to move A under C (would create cycle A->B->C->A)
            cycle_resp, _ = self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "task.move", "task_id": a_id, "new_parent_id": c_id}],
            })
            self.assertFalse(cycle_resp.ok)
            self.assertIn("cycle", str(cycle_resp.error.message).lower())

            # Try to move A under itself
            self_resp, _ = self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "task.move", "task_id": a_id, "new_parent_id": a_id}],
            })
            self.assertFalse(self_resp.ok)
            self.assertIn("cycle", str(self_resp.error.message).lower())
        finally:
            cleanup()

    # =========================================================================
    # task.status (separate from task.update)
    # =========================================================================

    def test_task_status_transitions(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "task.create", "name": "T", "goal": "g"}],
            })
            tid = self._call("task_list", {"group_id": gid})[0].result["tasks"][0]["id"]

            # planned -> active
            resp, _ = self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "task.status", "task_id": tid, "status": "active"}],
            })
            self.assertTrue(resp.ok)

            # active -> done
            resp2, _ = self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "task.status", "task_id": tid, "status": "done"}],
            })
            self.assertTrue(resp2.ok)

            # done -> archived (records archived_from)
            resp3, _ = self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "task.status", "task_id": tid, "status": "archived"}],
            })
            self.assertTrue(resp3.ok)

            # restore
            resp4, _ = self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "task.restore", "task_id": tid}],
            })
            self.assertTrue(resp4.ok)

            task = self._call("task_list", {"group_id": gid, "task_id": tid})[0].result["task"]
            self.assertEqual(task["status"], "done")
        finally:
            cleanup()

    def test_task_status_rejects_legacy_pending_token(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "task.create", "name": "T", "goal": "g"}],
            })
            tid = self._call("task_list", {"group_id": gid})[0].result["tasks"][0]["id"]

            resp, _ = self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "task.status", "task_id": tid, "status": "pending"}],
            })
            self.assertFalse(resp.ok)
            self.assertIn("invalid task status", str(resp.error.message).lower())
        finally:
            cleanup()

    def test_task_status_autosyncs_assignee_agent_state(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            create_resp, _ = self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "task.create", "name": "Stabilize sync", "goal": "reduce drift", "assignee": "peer1"}],
            })
            self.assertTrue(create_resp.ok, getattr(create_resp, "error", None))
            tid = self._call("task_list", {"group_id": gid})[0].result["tasks"][0]["id"]

            active_resp, _ = self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "task.status", "task_id": tid, "status": "active"}],
            })
            self.assertTrue(active_resp.ok, getattr(active_resp, "error", None))

            get_active, _ = self._call("context_get", {"group_id": gid})
            self.assertTrue(get_active.ok, getattr(get_active, "error", None))
            agents = get_active.result.get("agents", [])
            peer = [a for a in agents if a["id"] == "peer1"][0]
            self.assertEqual(peer.get("active_task_id"), tid)
            self.assertEqual(peer.get("focus"), "Stabilize sync")
            self.assertEqual(peer.get("what_changed"), f"{tid} -> active")

            done_resp, _ = self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "task.status", "task_id": tid, "status": "done"}],
            })
            self.assertTrue(done_resp.ok, getattr(done_resp, "error", None))

            get_done, _ = self._call("context_get", {"group_id": gid})
            self.assertTrue(get_done.ok, getattr(get_done, "error", None))
            agents_done = get_done.result.get("agents", [])
            peer_done = [a for a in agents_done if a["id"] == "peer1"][0]
            self.assertIsNone(peer_done.get("active_task_id"))
            self.assertEqual(peer_done.get("focus"), "")
            self.assertEqual(peer_done.get("what_changed"), f"{tid} -> done")
        finally:
            cleanup()

    # =========================================================================
    # overview.manual.update
    # =========================================================================

    def test_overview_manual_update(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()

            resp, _ = self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{
                    "op": "overview.manual.update",
                    "roles": ["lead", "implementer"],
                    "collaboration_mode": "pair programming",
                    "current_focus": "auth module",
                }],
            })
            self.assertTrue(resp.ok, getattr(resp, "error", None))

            get_resp, _ = self._call("context_get", {"group_id": gid})
            self.assertTrue(get_resp.ok)
            overview = get_resp.result.get("overview", {})
            manual = overview.get("manual", {})
            self.assertEqual(manual["roles"], ["lead", "implementer"])
            self.assertEqual(manual["collaboration_mode"], "pair programming")
            self.assertEqual(manual["current_focus"], "auth module")
            self.assertEqual(manual["updated_by"], "user")
        finally:
            cleanup()

    # =========================================================================
    # panorama projection
    # =========================================================================

    def test_panorama_mermaid_includes_agents_and_tasks(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()

            # Create a root task and set active
            self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "task.create", "name": "Phase 1", "goal": "deliver"}],
            })
            tid = self._call("task_list", {"group_id": gid})[0].result["tasks"][0]["id"]
            self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "task.status", "task_id": tid, "status": "active"}],
            })

            # Set flat agent state
            self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [
                    {"op": "agent.update", "agent_id": "foreman", "focus": "auth", "blockers": ["API key"], "next_action": "write tests", "active_task_id": tid},
                ],
            })

            get_resp, _ = self._call("context_get", {"group_id": gid})
            self.assertTrue(get_resp.ok)
            result = get_resp.result

            mermaid = str(result.get("panorama", {}).get("mermaid") or "")
            self.assertIn("graph TD", mermaid)
            self.assertIn("foreman", mermaid)
            self.assertIn(tid, mermaid)
            self.assertNotIn("mermaid", result.get("overview", {}))

            # Check flat agent state in agents section
            agents = result.get("agents", [])
            foreman_agent = [a for a in agents if a["id"] == "foreman"]
            self.assertEqual(len(foreman_agent), 1)
            self.assertEqual(foreman_agent[0]["focus"], "auth")
            self.assertEqual(foreman_agent[0]["blockers"], ["API key"])
            self.assertEqual(foreman_agent[0]["next_action"], "write tests")
        finally:
            cleanup()

    # =========================================================================
    # agent.clear
    # =========================================================================

    def test_agent_clear(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()

            # Set flat agent state
            self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "agent.update", "agent_id": "peer1", "focus": "coding", "blockers": ["dep waiting"]}],
            })

            # Verify state exists
            get1, _ = self._call("context_get", {"group_id": gid})
            agents = get1.result.get("agents", [])
            peer = [a for a in agents if a["id"] == "peer1"][0]
            self.assertEqual(peer.get("focus"), "coding")
            self.assertEqual(peer.get("blockers"), ["dep waiting"])

            # Clear state
            self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "agent.clear", "agent_id": "peer1"}],
            })

            # Verify fields are cleared
            get2, _ = self._call("context_get", {"group_id": gid})
            agents2 = get2.result.get("agents", [])
            peer2 = [a for a in agents2 if a["id"] == "peer1"][0]
            self.assertEqual(peer2.get("focus"), "")
            self.assertEqual(peer2.get("blockers"), [])
            self.assertEqual(peer2.get("next_action"), "")
        finally:
            cleanup()

    # =========================================================================
    # Permission checks
    # =========================================================================

    def test_peer_cannot_vision_update(self) -> None:
        """Peer agents cannot update vision (foreman/user only)."""
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()

            # Add a peer actor
            self._call("group_actor_add", {
                "group_id": gid, "by": "user",
                "actor_id": "peer-impl", "runtime": "codex",
            })

            # Peer tries to update vision
            resp, _ = self._call("context_sync", {
                "group_id": gid, "by": "peer-impl",
                "ops": [{"op": "vision.update", "vision": "hijacked"}],
            })
            self.assertFalse(resp.ok)
            self.assertIn("permission", str(resp.error.message).lower())
        finally:
            cleanup()

    def test_peer_can_create_task(self) -> None:
        """Any actor can create tasks."""
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()

            self._call("group_actor_add", {
                "group_id": gid, "by": "user",
                "actor_id": "peer-impl", "runtime": "codex",
            })

            resp, _ = self._call("context_sync", {
                "group_id": gid, "by": "peer-impl",
                "ops": [{"op": "task.create", "name": "peer task", "goal": "do it"}],
            })
            self.assertTrue(resp.ok, getattr(resp, "error", None))
        finally:
            cleanup()

    # =========================================================================
    # task_list with children
    # =========================================================================

    def test_task_list_single_includes_children(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()

            self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "task.create", "name": "Root", "goal": "r"}],
            })
            root_id = self._call("task_list", {"group_id": gid})[0].result["tasks"][0]["id"]

            self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [
                    {"op": "task.create", "name": "Child1", "goal": "c1", "parent_id": root_id},
                    {"op": "task.create", "name": "Child2", "goal": "c2", "parent_id": root_id},
                ],
            })

            # Get single task with children
            resp, _ = self._call("task_list", {"group_id": gid, "task_id": root_id})
            self.assertTrue(resp.ok)
            task = resp.result["task"]
            self.assertEqual(task["id"], root_id)
            children = task.get("children", [])
            self.assertEqual(len(children), 2)
            child_names = sorted([c["name"] for c in children])
            self.assertEqual(child_names, ["Child1", "Child2"])
        finally:
            cleanup()

    # =========================================================================
    # legacy presence.* ops are removed in v2
    # =========================================================================

    def test_legacy_presence_ops_rejected(self) -> None:
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()

            resp, _ = self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "presence.update", "agent_id": "legacy", "status": "working"}],
            })
            self.assertFalse(resp.ok)
            self.assertIn("unknown", str(resp.error.message).lower())
        finally:
            cleanup()


    # =========================================================================
    # meta.merge — project_status validation
    # =========================================================================

    def test_meta_merge_project_status_rejects_non_string(self) -> None:
        """project_status must be string or null — integer should be rejected."""
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            resp, _ = self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "meta.merge", "data": {"project_status": 123}}],
            })
            self.assertFalse(resp.ok)
            self.assertIn("string or null", str(resp.error.message).lower())
        finally:
            cleanup()

    def test_meta_merge_project_status_rejects_over_100_chars(self) -> None:
        """project_status exceeding 100 characters should be rejected."""
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            long_text = "x" * 101
            resp, _ = self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "meta.merge", "data": {"project_status": long_text}}],
            })
            self.assertFalse(resp.ok)
            self.assertIn("100", str(resp.error.message))
        finally:
            cleanup()

    def test_meta_merge_project_status_accepts_null(self) -> None:
        """project_status=null should be accepted (clears the label)."""
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            # First set a value
            resp1, _ = self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "meta.merge", "data": {"project_status": "优化中"}}],
            })
            self.assertTrue(resp1.ok, getattr(resp1, "error", None))
            # Then clear with null
            resp2, _ = self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "meta.merge", "data": {"project_status": None}}],
            })
            self.assertTrue(resp2.ok, getattr(resp2, "error", None))
        finally:
            cleanup()

    def test_meta_merge_peer_permission_denied(self) -> None:
        """Peer callers should be rejected from meta.merge."""
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            # Add a peer actor to the group
            self._call("actor_upsert", {
                "group_id": gid, "actor_id": "peer-worker",
                "role": "peer", "title": "Worker",
            })
            # Peer tries to meta.merge — should fail
            resp, _ = self._call("context_sync", {
                "group_id": gid, "by": "peer-worker",
                "ops": [{"op": "meta.merge", "data": {"project_status": "test"}}],
            })
            self.assertFalse(resp.ok)
            self.assertIn("permission denied", str(resp.error.message).lower())
        finally:
            cleanup()


    # =========================================================================
    # meta.merge — panorama_blueprint schema validation
    # =========================================================================

    _VALID_BLUEPRINT = {
        "version": 1,
        "style_note": "Test house",
        "gridSize": [3, 3, 3],
        "blockScale": 0.15,
        "blocks": [
            {"x": i % 3, "y": i // 9, "z": (i // 3) % 3, "color": "#6b7280", "order": i}
            for i in range(27)
        ],
    }

    def test_meta_merge_valid_blueprint_accepted(self) -> None:
        """A well-formed blueprint should be accepted."""
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            resp, _ = self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "meta.merge", "data": {"panorama_blueprint": self._VALID_BLUEPRINT}}],
            })
            self.assertTrue(resp.ok, getattr(resp, "error", None))
        finally:
            cleanup()

    def test_meta_merge_blueprint_null_accepted(self) -> None:
        """null blueprint should be accepted (clears the blueprint)."""
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            resp, _ = self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "meta.merge", "data": {"panorama_blueprint": None}}],
            })
            self.assertTrue(resp.ok, getattr(resp, "error", None))
        finally:
            cleanup()

    def test_meta_merge_blueprint_bad_version_rejected(self) -> None:
        """Blueprint with version != 1 should be rejected."""
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            bad_bp = {**self._VALID_BLUEPRINT, "version": 2}
            resp, _ = self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "meta.merge", "data": {"panorama_blueprint": bad_bp}}],
            })
            self.assertFalse(resp.ok)
            self.assertIn("version must be 1", str(resp.error.message))
        finally:
            cleanup()

    def test_meta_merge_blueprint_bad_gridsize_rejected(self) -> None:
        """Blueprint with invalid gridSize should be rejected."""
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            bad_bp = {**self._VALID_BLUEPRINT, "gridSize": [25, 3, 3]}
            resp, _ = self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "meta.merge", "data": {"panorama_blueprint": bad_bp}}],
            })
            self.assertFalse(resp.ok)
            self.assertIn("gridSize", str(resp.error.message))
        finally:
            cleanup()

    def test_meta_merge_blueprint_bad_order_rejected(self) -> None:
        """Blueprint with non-contiguous order values should be rejected."""
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            bad_blocks = [
                {"x": 0, "y": 0, "z": 0, "color": "#aaa", "order": 0},
                {"x": 1, "y": 0, "z": 0, "color": "#bbb", "order": 5},  # gap
            ]
            bad_bp = {**self._VALID_BLUEPRINT, "blocks": bad_blocks, "gridSize": [3, 1, 1]}
            resp, _ = self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "meta.merge", "data": {"panorama_blueprint": bad_bp}}],
            })
            self.assertFalse(resp.ok)
            self.assertIn("order values must cover", str(resp.error.message))
        finally:
            cleanup()

    def test_meta_merge_blueprint_coord_out_of_range_rejected(self) -> None:
        """Blueprint with block coordinates outside gridSize should be rejected."""
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            bad_blocks = [
                {"x": 10, "y": 0, "z": 0, "color": "#aaa", "order": 0},  # x >= gridSize[0]
            ]
            bad_bp = {**self._VALID_BLUEPRINT, "blocks": bad_blocks, "gridSize": [3, 3, 3]}
            resp, _ = self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "meta.merge", "data": {"panorama_blueprint": bad_bp}}],
            })
            self.assertFalse(resp.ok)
            self.assertIn("blocks[0].x", str(resp.error.message))
        finally:
            cleanup()


    def test_meta_merge_blueprint_over_500_blocks_rejected(self) -> None:
        """Blueprint with more than 500 blocks should be rejected."""
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            big_blocks = [
                {"x": i % 20, "y": (i // 20) % 20, "z": (i // 400) % 20, "color": "#aaa", "order": i}
                for i in range(501)
            ]
            bad_bp = {**self._VALID_BLUEPRINT, "blocks": big_blocks, "gridSize": [20, 20, 20]}
            resp, _ = self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "meta.merge", "data": {"panorama_blueprint": bad_bp}}],
            })
            self.assertFalse(resp.ok)
            self.assertIn("1..500", str(resp.error.message))
        finally:
            cleanup()

    def test_meta_merge_blueprint_bool_gridsize_rejected(self) -> None:
        """Blueprint with boolean in gridSize should be rejected (bool is int subclass)."""
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            bad_bp = {**self._VALID_BLUEPRINT, "gridSize": [True, 3, 3]}
            resp, _ = self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "meta.merge", "data": {"panorama_blueprint": bad_bp}}],
            })
            self.assertFalse(resp.ok)
            self.assertIn("gridSize", str(resp.error.message))
        finally:
            cleanup()

    def test_meta_merge_blueprint_nan_blockscale_rejected(self) -> None:
        """Blueprint with NaN blockScale should be rejected."""
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            bad_bp = {**self._VALID_BLUEPRINT, "blockScale": float("nan")}
            resp, _ = self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "meta.merge", "data": {"panorama_blueprint": bad_bp}}],
            })
            self.assertFalse(resp.ok)
            self.assertIn("finite positive", str(resp.error.message))
        finally:
            cleanup()

    def test_meta_merge_blueprint_missing_style_note_rejected(self) -> None:
        """Blueprint without style_note key should be rejected."""
        _, cleanup = self._with_home()
        try:
            gid = self._create_group()
            bad_bp = {k: v for k, v in self._VALID_BLUEPRINT.items() if k != "style_note"}
            resp, _ = self._call("context_sync", {
                "group_id": gid, "by": "user",
                "ops": [{"op": "meta.merge", "data": {"panorama_blueprint": bad_bp}}],
            })
            self.assertFalse(resp.ok)
            self.assertIn("style_note is required", str(resp.error.message))
        finally:
            cleanup()


if __name__ == "__main__":
    unittest.main()
