import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


class TestWebManifestStatic(unittest.TestCase):
    def test_manifest_is_served_by_staticfiles_without_attachment_header(self) -> None:
        old_home = os.environ.get("CCCC_HOME")
        old_dist = os.environ.get("CCCC_WEB_DIST")
        with tempfile.TemporaryDirectory() as home_dir, tempfile.TemporaryDirectory() as dist_dir:
            os.environ["CCCC_HOME"] = home_dir
            os.environ["CCCC_WEB_DIST"] = dist_dir
            try:
                dist_path = Path(dist_dir)
                (dist_path / "index.html").write_text("<html><body>ok</body></html>", encoding="utf-8")
                (dist_path / "manifest.webmanifest").write_text('{"name":"CCCC"}\n', encoding="utf-8")

                from cccc.ports.web.app import create_app

                client = TestClient(create_app())
                resp = client.get("/ui/manifest.webmanifest")

                self.assertEqual(resp.status_code, 200)
                self.assertTrue(str(resp.headers.get("content-type") or "").startswith("application/manifest+json"))
                self.assertNotIn("content-disposition", resp.headers)
                self.assertEqual(resp.text, '{"name":"CCCC"}\n')
            finally:
                if old_home is None:
                    os.environ.pop("CCCC_HOME", None)
                else:
                    os.environ["CCCC_HOME"] = old_home
                if old_dist is None:
                    os.environ.pop("CCCC_WEB_DIST", None)
                else:
                    os.environ["CCCC_WEB_DIST"] = old_dist


if __name__ == "__main__":
    unittest.main()
