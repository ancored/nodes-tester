"""Local-only checks for rule sources."""

import json
import tempfile
import unittest
from pathlib import Path

from nodes_admin.rules import update, validate_rules


class RulesTests(unittest.TestCase):
    def test_failed_download_keeps_old_file_and_marker_only_tracks_change(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / "config/singbox"
            source.mkdir(parents=True)
            target = root / "target/rules"
            target.mkdir(parents=True)
            (target / "remote.srs").write_bytes(b"old")
            (source / "rules").mkdir()
            (source / "rules/local.json").write_bytes(b"new")
            (source / "rules.json").write_text(json.dumps({
                "version": 1,
                "rules": [
                    {"dest": "remote.srs", "url": "https://example.org/remote.srs"},
                    {"dest": "local.json", "file": "rules/local.json"},
                ],
            }), encoding="utf-8")
            marker = root / "marker"

            def failed(_url):
                raise OSError("download failed")

            changed = update(root / "config", root / "target", marker, downloader=failed)
            self.assertEqual(changed, ["local.json"])
            self.assertEqual((target / "remote.srs").read_bytes(), b"old")
            self.assertEqual((target / "local.json").read_bytes(), b"new")
            # A changed rule file alone is reloaded by sing-box without a restart.
            self.assertFalse(marker.exists())
            self.assertEqual(update(root / "config", root / "target", marker, downloader=failed), [])
            self.assertFalse(marker.exists())
            (source / "base.json").write_bytes(b"{}")
            self.assertEqual(update(root / "config", root / "target", marker, downloader=failed), ["base.json"])
            self.assertTrue(marker.exists())

    def test_invalid_dest_and_file_path_rejected_before_writes(self):
        for entry in (
            {"dest": "../bad", "url": "https://example.org/a"},
            {"dest": "good", "file": "rules/../bad"},
            {"dest": "good", "file": "rules/local.txt"},
            {"dest": "good", "url": "file:///etc/passwd"},
        ):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                validate_rules({"version": 1, "rules": [entry]})
        with self.assertRaises(ValueError):
            validate_rules({"version": True, "rules": []})


if __name__ == "__main__":
    unittest.main()
