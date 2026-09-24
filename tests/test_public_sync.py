import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PublicSyncManifestTests(unittest.TestCase):
    def test_manifest_is_allowlist_only(self):
        manifest = json.loads((ROOT / "scripts" / "public_sync_manifest.json").read_text())
        paths = manifest["copy"] + manifest["transform"]
        forbidden = ("data/", "docs/", "deploy/", "migrations/", "frontend/district", "modal")
        self.assertTrue(paths)
        self.assertEqual(len(paths), len(set(paths)))
        for path in paths:
            self.assertFalse(path.startswith(forbidden), path)
            self.assertNotIn("..", Path(path).parts)

    def test_public_owned_files_are_not_overwritten(self):
        manifest = json.loads((ROOT / "scripts" / "public_sync_manifest.json").read_text())
        copied = set(manifest["copy"])
        for path in ("backend/server.js", "frontend/feed.js", "frontend/alert.js",
                     "frontend/journalist-data.js", "migrations/0001_baseline.sql",
                     ".claude/skills/onboard-school/SKILL.md"):
            self.assertNotIn(path, copied)


if __name__ == "__main__":
    unittest.main()
