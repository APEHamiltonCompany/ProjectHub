from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tracker import matcher
from tracker import tracker as tracker_mod
from tracker.hub.note_cache import NoteCache


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class NoteCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "note.md"
        self.path.write_text("one", encoding="utf-8")
        self.clock = Clock()
        self.cache = NoteCache(ttl=30, clock=self.clock)

    def tearDown(self):
        self.tmp.cleanup()

    def change(self, text: str):
        self.path.write_text(text, encoding="utf-8")
        stamp = self.path.stat().st_mtime + 5
        os.utime(self.path, (stamp, stamp))

    def test_reuses_text_within_ttl_and_rereads_after_change(self):
        self.assertEqual(self.cache.read(self.path), "one")
        self.change("two!")
        self.assertEqual(self.cache.read(self.path), "one")
        self.clock.now = 31
        self.assertEqual(self.cache.read(self.path), "two!")

    def test_unchanged_file_is_not_read_again(self):
        self.cache.read(self.path)
        self.clock.now = 31
        with mock.patch("tracker.notes.read_text", side_effect=AssertionError("read again")):
            self.assertEqual(self.cache.read(self.path), "one")

    def test_missing_file_forget_and_clear(self):
        missing = Path(self.tmp.name) / "gone.md"
        self.assertIsNone(self.cache.read(missing))
        self.assertIsNone(self.cache.read(None))
        self.cache.read(self.path)
        self.change("three")
        self.cache.forget(self.path)
        self.assertEqual(self.cache.read(self.path), "three")
        self.change("four!")
        self.cache.clear()
        self.assertEqual(self.cache.read(self.path), "four!")

    def test_prefetch_fills_the_cache(self):
        other = Path(self.tmp.name) / "other.md"
        other.write_text("x", encoding="utf-8")
        self.cache.prefetch([self.path, other, None])
        with mock.patch("tracker.notes.read_text", side_effect=AssertionError("read again")):
            self.assertEqual(self.cache.read(other), "x")
            self.assertEqual(self.cache.read(self.path), "one")


class ArchivedTrackerRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        self.active = self.vault / "Acme SO-100001"
        self.done = self.vault / "Beta SO-100002"
        for folder in (self.active, self.done):
            folder.mkdir(parents=True)
            (folder / (folder.name + ".md")).write_text("---\nlevel: L1\n---\n# %s\n" % folder.name, encoding="utf-8")
        self.state_path = self.root / "state.json"
        self.snapshot_path = self.root / "snapshot.json"
        self.config = self.root / "config.yaml"
        self.config.write_text(yaml.safe_dump({
            "vault": str(self.vault),
            "state_path": str(self.state_path),
            "snapshot_path": str(self.snapshot_path),
            "log_path": str(self.root / "last_run.log"),
        }), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def run_tracker(self):
        self.assertEqual(tracker_mod.main(["--config", str(self.config), "--no-dashboard"]), 0)
        return {row["key"]: row for row in json.loads(self.snapshot_path.read_text(encoding="utf-8"))["projects"]}

    def test_archived_folder_is_not_read_and_keeps_its_numbers(self):
        first = self.run_tracker()
        key = "SO-100002"
        self.assertIn(key, first)
        self.assertEqual(first[key]["level"], "L1")
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        state["archived_projects"] = {key: {"rel": matcher.relative_folder(self.vault, self.done), "at": "2026-11-20T10:00:00"}}
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        (self.done / (self.done.name + ".md")).write_text("---\nlevel: L3\n---\n", encoding="utf-8")

        opened = []
        real = matcher.project_from_folder

        def spy(folder, customer=""):
            opened.append(Path(folder).name)
            return real(folder, customer)

        with mock.patch.object(matcher, "project_from_folder", side_effect=spy):
            second = self.run_tracker()
        self.assertNotIn(self.done.name, opened)
        self.assertIn(self.active.name, opened)
        self.assertTrue(second[key]["archived"])
        self.assertEqual(second[key]["level"], "L1")
        self.assertEqual(second[key]["so"], ["SO-100002"])
        self.assertEqual(second[key]["archived_at"], "2026-11-20T10:00:00")
        self.assertFalse(second["SO-100001"].get("archived"))
        self.assertTrue(self.done.is_dir())

        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertIn(key, state["archived_projects"])
        state["archived_projects"] = {}
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        third = self.run_tracker()
        self.assertFalse(third[key].get("archived"))
        self.assertEqual(third[key]["level"], "L3")


if __name__ == "__main__":
    unittest.main()
