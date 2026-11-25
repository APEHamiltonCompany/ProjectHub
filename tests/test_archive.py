from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tracker.hub import tasks as tasks_mod
from tracker.hub.app import create_app
from tests.test_hub import HOST, ORIGIN

TASK_ID = "m-1a2b3c4d"
OTHER_ID = "m-2b3c4d5e"
PERSONAL_TEXT = """# My tasks

**My tasks**
- [ ] Write the SOP \U0001F4C5 2026-11-20 \U0001F194 m-1a2b3c4d
    - \U0001F4DD draft outline
- [ ] Book the room \U0001F194 m-2b3c4d5e
"""


class ArchiveTextTests(unittest.TestCase):
    def test_archive_moves_task_and_hides_it(self):
        text = tasks_mod.archive_manual(PERSONAL_TEXT, TASK_ID)
        self.assertIn("**Archived**", text)
        self.assertTrue(text.rstrip().endswith("draft outline"))
        self.assertEqual([row["id"] for row in tasks_mod.parse_lines(text)], [OTHER_ID])
        archived = tasks_mod.parse_lines(text, archived=True)
        self.assertEqual([row["id"] for row in archived], [TASK_ID])
        self.assertEqual(archived[0]["notes"], "draft outline")
        self.assertIn("\U0001F4DD draft outline", text)
        again = tasks_mod.archive_manual(text, OTHER_ID)
        self.assertEqual(again.count("**Archived**"), 1)
        self.assertEqual(
            [row["id"] for row in tasks_mod.parse_lines(again, archived=True)], [TASK_ID, OTHER_ID]
        )
        self.assertEqual(tasks_mod.parse_lines(again), [])
        with self.assertRaises(KeyError):
            tasks_mod.archive_manual(again, TASK_ID)

    def test_unarchive_restores_task(self):
        text = tasks_mod.archive_manual(PERSONAL_TEXT, TASK_ID)
        restored = tasks_mod.unarchive_manual(text, TASK_ID)
        self.assertNotIn("**Archived**", restored)
        self.assertEqual(
            sorted(row["id"] for row in tasks_mod.parse_lines(restored)), sorted([TASK_ID, OTHER_ID])
        )
        self.assertEqual(tasks_mod.parse_lines(restored, archived=True), [])
        self.assertIn("\U0001F4DD draft outline", restored)
        with self.assertRaises(KeyError):
            tasks_mod.unarchive_manual(restored, TASK_ID)

    def test_insert_before_archive_heading(self):
        text = "**Archived**\n- [ ] Old one \U0001F194 m-9f9f9f9f\n"
        new_text = tasks_mod.insert_manual(text, ["- [ ] Fresh \U0001F194 m-8e8e8e8e"])
        self.assertLess(new_text.index("**My tasks**"), new_text.index("**Archived**"))
        self.assertLess(new_text.index("Fresh"), new_text.index("**Archived**"))
        self.assertEqual([row["id"] for row in tasks_mod.parse_lines(new_text)], ["m-8e8e8e8e"])
        self.assertEqual(
            [row["id"] for row in tasks_mod.parse_lines(new_text, archived=True)], ["m-9f9f9f9f"]
        )

    def test_tracker_block_after_archive_is_active(self):
        text = (
            "**My tasks**\n"
            "- [ ] Write the SOP \U0001F194 m-1a2b3c4d\n"
            "\n"
            "**Archived**\n"
            "- [ ] Old one \U0001F194 m-9f9f9f9f\n"
            "\n"
            "<!-- tracker:start (auto-managed) -->\n"
            "**Pre-Fabrication**\n"
            "- [ ] Review CPR/SO \U0001F194 p0207000-review_cpr_so\n"
            "<!-- tracker:end -->\n"
        )
        self.assertEqual(
            [row["id"] for row in tasks_mod.parse_lines(text)], [TASK_ID, "p0207000-review_cpr_so"]
        )
        self.assertEqual(
            [row["id"] for row in tasks_mod.parse_lines(text, archived=True)], ["m-9f9f9f9f"]
        )


class ArchiveApiTests(unittest.TestCase):
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
        self.app = create_app(self.config_path, port=8765, no_refresh=True, runner=lambda: 0)
        self.app.testing = True
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

    def dashboard(self) -> str:
        return self.get("/").get_data(as_text=True)

    def test_archive_and_restore_personal_task(self):
        html = self.dashboard()
        self.assertIn('data-action="task-archive" data-key="" data-id="%s"' % TASK_ID, html)
        self.assertLess(html.index('data-action="task-edit"'), html.index('data-action="task-archive"'))
        self.assertLess(html.index('data-action="task-archive"'), html.index('data-action="task-delete"'))
        archived = self.post("/api/tasks/archive", {"key": "", "id": TASK_ID})
        self.assertEqual(archived.status_code, 200)
        self.assertEqual(archived.get_json(), {"ok": True, "id": TASK_ID})
        html = self.dashboard()
        self.assertNotIn('data-action="task-archive" data-key="" data-id="%s"' % TASK_ID, html)
        self.assertIn("Archived (1)", html)
        self.assertIn('data-action="task-unarchive" data-key="" data-id="%s"' % TASK_ID, html)
        restored = self.post("/api/tasks/unarchive", {"key": "", "id": TASK_ID})
        self.assertEqual(restored.status_code, 200)
        html = self.dashboard()
        self.assertIn('data-action="task-archive" data-key="" data-id="%s"' % TASK_ID, html)
        self.assertNotIn("Archived (1)", html)
        self.assertEqual(self.post("/api/tasks/archive", {"key": "", "id": "m-deadbeef"}).status_code, 404)
        self.assertEqual(self.post("/api/tasks/archive", {"key": "", "id": "p0207000-review"}).status_code, 400)
        self.assertEqual(self.post("/api/tasks/unarchive", {"key": "", "id": "p0207000-review"}).status_code, 400)

    def test_new_personal_task_not_archived_when_archive_is_last(self):
        self.assertEqual(self.post("/api/tasks/archive", {"key": "", "id": OTHER_ID}).status_code, 200)
        self.assertTrue(self.personal.read_text(encoding="utf-8").rstrip().endswith(OTHER_ID))
        added = self.post("/api/tasks/add", {"key": "", "description": "Fresh task", "notes": "some detail"})
        self.assertEqual(added.status_code, 200)
        new_id = added.get_json()["id"]
        text = self.personal.read_text(encoding="utf-8")
        self.assertLess(text.index("Fresh task"), text.index("**Archived**"))
        self.assertEqual([row["id"] for row in tasks_mod.parse_lines(text)], [TASK_ID, new_id])
        self.assertEqual([row["id"] for row in tasks_mod.parse_lines(text, archived=True)], [OTHER_ID])
        fresh = next(row for row in tasks_mod.parse_lines(text) if row["id"] == new_id)
        self.assertEqual(fresh["notes"], "some detail")
        html = self.dashboard()
        self.assertIn('data-action="task-archive" data-key="" data-id="%s"' % new_id, html)


if __name__ == "__main__":
    unittest.main()
