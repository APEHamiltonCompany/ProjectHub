from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tracker import notes
from tracker.hub import tasks as tasks_mod
from tracker.hub.app import create_app
from tests.test_hub import HOST, ORIGIN

TASK_ID = "m-1a2b3c4d"
TASK_TITLE = "Write the SOP"
PERSONAL_TEXT = """# My tasks

**My tasks**
- [ ] Write the SOP \U0001F4C5 2026-11-20 \U0001F194 m-1a2b3c4d
    - \U0001F4DD draft outline
"""


class TaskPageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.personal = self.vault / "My Tasks.md"
        self.personal.write_text(PERSONAL_TEXT, encoding="utf-8")
        self.rules_path = self.root / "rules.yaml"
        self.rules_path.write_text(
            yaml.safe_dump({"stages": ["Pre-Fabrication", "System Test", "Closeout", "Completion"], "workflow": []}),
            encoding="utf-8",
        )
        self.state_path = self.root / "state.json"
        self.snapshot_path = self.root / "snapshot.json"
        self.config_path = self.root / "config.yaml"
        self.cfg = {
            "vault": str(self.vault),
            "personal_tasks_path": str(self.personal),
            "exclude_folders": ["General"],
            "state_path": str(self.state_path),
            "snapshot_path": str(self.snapshot_path),
            "log_path": str(self.root / "last_run.log"),
            "backup_root": str(self.root / "backups"),
            "templates_dir": str(self.root / "templates"),
            "rules_path": str(self.rules_path),
            "me": {"name": "Anthony Zunino"},
            "hub": {"port": 8765, "refresh_minutes": 15, "toast": False},
        }
        self.config_path.write_text(yaml.safe_dump(self.cfg), encoding="utf-8")
        self.state_path.write_text(
            json.dumps({"pending": {}, "unmatched": [], "email_overrides": {}, "due_overrides": {}}),
            encoding="utf-8",
        )
        self.snapshot_path.write_text(
            json.dumps(
                {
                    "generated": "2026-11-10T06:30:00-08:00",
                    "today": "2026-11-10",
                    "projects": [],
                    "pending": [],
                    "unmatched": [],
                }
            ),
            encoding="utf-8",
        )
        self.launched = []
        self.app = create_app(self.config_path, port=8765, no_refresh=True, runner=lambda: 0)
        self.app.testing = True
        self.app.config["LAUNCHER"] = self.launched.append
        self.client = self.app.test_client()
        self.token = self.app.config["HUB_TOKEN"]

    def tearDown(self):
        self.tmp.cleanup()

    def post(self, url: str, payload: dict):
        headers = dict(HOST)
        headers["X-Hub-Token"] = self.token
        headers["Origin"] = ORIGIN
        return self.client.post(url, json=payload, headers=headers)

    def get(self, url: str):
        return self.client.get(url, headers=HOST)

    def page(self, task_id: str = TASK_ID) -> str:
        return self.get("/t/%s" % task_id).get_data(as_text=True)

    def note_path(self) -> Path:
        return self.vault / "My Tasks" / TASK_TITLE / (TASK_TITLE + ".md")

    def note_data(self) -> tuple[dict, str]:
        text, _nl, _bom, _enc = notes.read_text(self.note_path())
        data, _raw, body = notes.split_frontmatter(text)
        return data, body

    def task_line(self) -> str:
        text, _nl, _bom, _enc = notes.read_text(self.personal)
        for row in tasks_mod.parse_lines(text):
            if row["id"] == TASK_ID:
                return row["line"]
        return ""

    def test_page_creates_note_and_token(self):
        response = self.get("/t/%s" % TASK_ID)
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("<h2>Notes</h2>", html)
        self.assertIn("<h2>Links</h2>", html)
        self.assertIn("<h2>Folder</h2>", html)
        self.assertIn("<h2>Time</h2>", html)
        self.assertIn(TASK_TITLE, html)
        self.assertIn("draft outline", html)
        self.assertTrue(self.note_path().is_file())
        data, body = self.note_data()
        self.assertEqual(data["task_id"], TASK_ID)
        self.assertEqual(data["folder"], "")
        self.assertEqual(data["launch"], "")
        self.assertEqual(data["links"], [])
        self.assertIn("## Notes", body)
        self.assertIn("[note:: My Tasks/%s/%s.md]" % (TASK_TITLE, TASK_TITLE), self.task_line())

    def test_unknown_task_404(self):
        self.assertEqual(self.get("/t/m-deadbeef").status_code, 404)
        self.assertEqual(self.get("/t/p0207000-review").status_code, 404)

    def test_second_get_does_not_duplicate(self):
        self.get("/t/%s" % TASK_ID)
        self.get("/t/%s" % TASK_ID)
        text, _nl, _bom, _enc = notes.read_text(self.personal)
        self.assertEqual(text.count("[note::"), 1)
        self.assertEqual(len(list((self.vault / "My Tasks").iterdir())), 1)

    def test_links_add_and_remove(self):
        self.get("/t/%s" % TASK_ID)
        local = self.root / "spec.txt"
        local.write_text("spec", encoding="utf-8")
        added = self.post(
            "/api/task/links/add",
            {"task_id": TASK_ID, "title": "Spec", "url": "https://example.com/spec"},
        )
        self.assertEqual(added.status_code, 200)
        self.assertEqual(len(added.get_json()["links"]), 1)
        added = self.post("/api/task/links/add", {"task_id": TASK_ID, "title": "Local", "url": str(local)})
        self.assertEqual(added.status_code, 200)
        self.assertEqual(len(added.get_json()["links"]), 2)
        self.assertIn("https://example.com/spec", self.page())
        self.assertIn(str(local), self.page())
        missing = self.post(
            "/api/task/links/add", {"task_id": TASK_ID, "title": "Gone", "url": str(self.root / "nope.txt")}
        )
        self.assertEqual(missing.status_code, 400)
        removed = self.post("/api/task/links/remove", {"task_id": TASK_ID, "index": 0})
        self.assertEqual(removed.status_code, 200)
        links = removed.get_json()["links"]
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["url"], str(local))
        data, _body = self.note_data()
        self.assertEqual(data["links"], [{"title": "Local", "url": str(local)}])
        self.assertEqual(self.post("/api/task/links/remove", {"task_id": TASK_ID, "index": 9}).status_code, 400)

    def test_folder_set_and_reset(self):
        self.get("/t/%s" % TASK_ID)
        folder = self.root / "work"
        folder.mkdir()
        set_response = self.post("/api/task/folder", {"task_id": TASK_ID, "folder": str(folder)})
        self.assertEqual(set_response.status_code, 200)
        self.assertEqual(set_response.get_json()["folder"], str(folder))
        self.assertIn(str(folder), self.page())
        data, _body = self.note_data()
        self.assertEqual(data["folder"], str(folder))
        bad = self.post("/api/task/folder", {"task_id": TASK_ID, "folder": str(self.root / "nope")})
        self.assertEqual(bad.status_code, 400)
        reset = self.post("/api/task/folder", {"task_id": TASK_ID, "folder": ""})
        self.assertEqual(reset.status_code, 200)
        self.assertEqual(reset.get_json()["folder"], str(self.note_path().parent))
        data, _body = self.note_data()
        self.assertEqual(data["folder"], "")

    def test_launch_set_and_launch(self):
        self.get("/t/%s" % TASK_ID)
        exe = self.root / "Tool.exe"
        exe.write_bytes(b"MZ")
        set_response = self.post("/api/task/launch_set", {"key": "", "task_id": TASK_ID, "target": str(exe)})
        self.assertEqual(set_response.status_code, 200)
        self.assertEqual(set_response.get_json()["launch"], str(exe))
        self.assertEqual(set_response.get_json()["source"], "task")
        data, _body = self.note_data()
        self.assertEqual(data["launch"], str(exe))
        self.assertIn('data-action="task-launch" data-key="" data-task-id="%s"' % TASK_ID, self.page())
        self.assertIn('data-action="task-launch" data-key="" data-task-id="%s"' % TASK_ID, self.get("/").get_data(as_text=True))
        launched = self.post("/api/task/launch", {"key": "", "task_id": TASK_ID})
        self.assertEqual(launched.status_code, 200)
        self.assertEqual(launched.get_json()["launched"], str(exe))
        self.assertEqual(self.launched, [str(exe)])
        cleared = self.post("/api/task/launch_set", {"key": "", "task_id": TASK_ID, "target": ""})
        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(cleared.get_json()["launch"], "")
        self.assertEqual(self.post("/api/task/launch", {"key": "", "task_id": TASK_ID}).status_code, 404)
        self.post("/api/task/launch_set", {"key": "", "task_id": TASK_ID, "target": str(exe)})
        reset = self.post("/api/task/launch_set", {"key": "", "task_id": TASK_ID, "reset": True})
        self.assertEqual(reset.status_code, 200)
        self.assertEqual(reset.get_json()["launch"], "")
        data, _body = self.note_data()
        self.assertNotIn("launch", data)

    def test_note_save_conflict_hash(self):
        self.get("/t/%s" % TASK_ID)
        current = hashlib.sha256(self.note_path().read_bytes()).hexdigest()
        text, _nl, _bom, _enc = notes.read_text(self.note_path())
        conflict = self.post(
            "/api/note/save", {"task_id": TASK_ID, "text": text + "\nmore\n", "base_hash": "stale"}
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.get_json()["error"], "conflict")
        saved = self.post(
            "/api/note/save", {"task_id": TASK_ID, "text": text + "\nmore\n", "base_hash": current}
        )
        self.assertEqual(saved.status_code, 200)
        self.assertIn("more", notes.read_text(self.note_path())[0])
        self.assertNotEqual(saved.get_json()["hash"], current)

    def test_note_toggle_uses_task_note(self):
        self.get("/t/%s" % TASK_ID)
        text, _nl, _bom, _enc = notes.read_text(self.note_path())
        notes.write_text(self.note_path(), text + "- [ ] check me\n", "\n", False, "utf-8")
        lines = notes.read_text(self.note_path())[0].split("\n")
        index = lines.index("- [ ] check me")
        toggled = self.post(
            "/api/note/toggle",
            {"task_id": TASK_ID, "line_index": index, "expected_line": "- [ ] check me", "mark": "x"},
        )
        self.assertEqual(toggled.status_code, 200)
        self.assertEqual(toggled.get_json()["line"], "- [x] check me")

    def test_dashboard_links_to_task_page(self):
        html = self.get("/").get_data(as_text=True)
        self.assertIn('href="/t/%s"' % TASK_ID, html)


class TaskNoteTokenTests(unittest.TestCase):
    def test_parse_lines_strips_note_token(self):
        rows = tasks_mod.parse_lines(
            "- [ ] Task \U0001F4C5 2026-11-20 \U0001F194 m-bbbb2222 [note:: My Tasks/Task/Task.md]"
        )
        self.assertEqual(rows[0]["title"], "Task")
        self.assertEqual(rows[0]["note"], "My Tasks/Task/Task.md")

    def test_update_manual_keeps_note_token(self):
        text = "- [ ] Task \U0001F4C5 2026-11-20 \U0001F194 m-bbbb2222 [note:: My Tasks/Task/Task.md]"
        new_text = tasks_mod.update_manual(text, "m-bbbb2222", description="Renamed")
        self.assertIn("Renamed", new_text)
        self.assertIn("[note:: My Tasks/Task/Task.md]", new_text)

    def test_spawn_next_carries_note_but_not_time(self):
        text = (
            "- [x] Weekly report \U0001F501 every week on Friday \U0001F4C5 2026-11-13 "
            "\U0001F194 m-aaaa1111 [time:: 2026-11-13=1.5] [note:: My Tasks/Weekly report/Weekly report.md]"
        )
        new_text, row = tasks_mod.spawn_next(text, "m-aaaa1111", date(2026, 11, 16))
        self.assertIsNotNone(row)
        self.assertIn("[note:: My Tasks/Weekly report/Weekly report.md]", row["line"])
        self.assertNotIn("[time::", row["line"])
        self.assertIn("[time:: 2026-11-13=1.5]", new_text.split("\n")[1])
        self.assertEqual(row["due"], date(2026, 11, 20))

    def test_time_entries_keep_note_token(self):
        text = "- [ ] Task \U0001F4C5 2026-11-20 \U0001F194 m-cccc3333 [note:: My Tasks/Task/Task.md]"
        new_text, _row = tasks_mod.add_time_entry(text, task_id="m-cccc3333", day=date(2026, 11, 16), hours=2)
        self.assertIn("[time:: 2026-11-16=2]", new_text)
        self.assertIn("[note:: My Tasks/Task/Task.md]", new_text)


if __name__ == "__main__":
    unittest.main()
