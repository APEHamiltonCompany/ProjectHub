from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tracker import matcher
from tracker.hub.app import create_app
from tests.test_hub import HOST, NOTE_TEXT, ORIGIN, PENDING, TEMPLATE_TEXT


class ProjectsAdminTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.templates = self.root / "templates"
        self.templates.mkdir()
        (self.templates / "Project Template.md").write_text(TEMPLATE_TEXT, encoding="utf-8")
        self.folder = self.vault / "Acme SO-1 0207000"
        self.folder.mkdir()
        self.note = self.folder / "Acme SO-1 0207000.md"
        self.note.write_text(NOTE_TEXT, encoding="utf-8")
        self.state_path = self.root / "state.json"
        self.snapshot_path = self.root / "snapshot.json"
        self.config_path = self.root / "config.yaml"
        self.backup_root = self.root / "backups"
        self.cfg = {
            "vault": str(self.vault),
            "exclude_folders": ["General"],
            "state_path": str(self.state_path),
            "snapshot_path": str(self.snapshot_path),
            "log_path": str(self.root / "last_run.log"),
            "backup_root": str(self.backup_root),
            "templates_dir": str(self.templates),
            "rules_path": str(ROOT / "tracker" / "rules.yaml"),
            "me": {"name": "Anthony Zunino"},
            "hub": {"port": 8765, "refresh_minutes": 15, "toast": False},
        }
        self.config_path.write_text(yaml.safe_dump(self.cfg), encoding="utf-8")
        self.state_path.write_text(
            json.dumps(
                {
                    "pending": {"msg-1": dict(PENDING)},
                    "unmatched": [],
                    "email_overrides": {},
                    "due_overrides": {},
                    "tick_log": [],
                    "user_links": {},
                }
            ),
            encoding="utf-8",
        )
        self.snapshot_path.write_text(json.dumps(self.snapshot()), encoding="utf-8")
        self.runs = []
        self.app = create_app(
            self.config_path, port=8765, no_refresh=True, runner=lambda: self.runs.append(1) or 0
        )
        self.app.testing = True
        self.client = self.app.test_client()
        self.token = self.app.config["HUB_TOKEN"]

    def tearDown(self):
        self.tmp.cleanup()

    def snapshot(self) -> dict:
        return {
            "generated": "2026-11-10T06:30:00-08:00",
            "today": "2026-11-10",
            "projects": [
                {
                    "key": "0207000",
                    "name": "Acme SO-1 0207000",
                    "folder": str(self.folder),
                    "note_path": str(self.note),
                    "customer": "Acme",
                    "project_no": "0207000",
                    "so": [],
                    "qte": ["QTE-1"],
                    "level": "L1",
                    "stage": "Pre-Fabrication",
                    "assigned": "2026-11-02",
                    "ship_date": "2026-12-01",
                    "design_need_by": "",
                    "sharepoint": "https://example.com/sp",
                    "cpr": "",
                    "rmt": "",
                    "team": ["A B"],
                    "links": [],
                    "triggers": {},
                    "tasks": [
                        {
                            "id": "p0207000-review_cpr_so",
                            "title": "Review CPR/SO",
                            "stage": "Pre-Fabrication",
                            "due": "2026-11-02",
                            "status": "open",
                            "mark": " ",
                            "source": "tracker",
                        }
                    ],
                    "emails": [
                        {
                            "date": "2026-11-09T08:00:00-08:00",
                            "sender": "Diane Martin <diane@example.com>",
                            "subject": "Acme kickoff",
                            "trigger": "kickoff",
                            "reply": False,
                            "message_id": "msg-2",
                        }
                    ],
                }
            ],
            "pending": [dict(PENDING)],
            "unmatched": [],
        }

    def post(self, url: str, payload: dict, token: str | None = None):
        headers = dict(HOST)
        headers["X-Hub-Token"] = self.token if token is None else token
        headers["Origin"] = ORIGIN
        return self.client.post(url, json=payload, headers=headers)

    def state(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def test_dashboard_banner_reads_state(self):
        page = self.client.get("/", headers=HOST).get_data(as_text=True)
        self.assertIn(PENDING["subject"], page)
        dismissed = self.post("/api/pending/dismiss", {"message_id": "msg-1"})
        self.assertEqual(dismissed.status_code, 200)
        page = self.client.get("/", headers=HOST).get_data(as_text=True)
        self.assertNotIn(PENDING["subject"], page)
        self.assertEqual(self.runs, [])

    def test_simulate_adds_test_entry(self):
        response = self.post("/api/pending/simulate", {})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["message_id"].startswith("test-"))
        entry = self.state()["pending"][payload["message_id"]]
        self.assertTrue(entry["test"])
        self.assertEqual(entry["status"], "new")
        self.assertEqual(entry["parsed"]["customer"], "TEST Customer")
        self.assertEqual(entry["team"], ["Anthony Zunino"])
        self.assertEqual(entry["checklist"], ["AE Order Review Checklist"])
        self.assertTrue(entry["suggested_folder"].startswith("TEST Customer QTE-9"))
        page = self.client.get("/", headers=HOST).get_data(as_text=True)
        self.assertIn(payload["subject"], page)
        self.assertIn("TEST", page)

    def test_create_from_simulated_entry(self):
        message_id = self.post("/api/pending/simulate", {}).get_json()["message_id"]
        created = self.post("/api/pending/create", {"message_id": message_id})
        self.assertEqual(created.status_code, 200)
        payload = created.get_json()
        folder = Path(payload["folder"])
        self.assertTrue(folder.is_dir())
        self.assertTrue((folder / (folder.name + ".md")).is_file())
        self.assertEqual(payload["key"], folder.name.split()[-1])
        self.assertEqual(payload["key_url"], "/p/" + payload["key"])
        self.assertEqual(self.state()["pending"][message_id]["status"], "created")
        self.assertEqual(len(self.runs), 1)

        again = self.post("/api/pending/create", {"message_id": message_id})
        self.assertEqual(again.status_code, 200)
        body = again.get_json()
        self.assertTrue(body["already"])
        self.assertEqual(body["folder"], payload["folder"])
        self.assertEqual(body["key"], payload["key"])

    def test_unknown_pending_entry_returns_404(self):
        response = self.post("/api/pending/create", {"message_id": "nope"})
        self.assertEqual(response.status_code, 404)
        self.assertIn("already handled", response.get_json()["error"])

    def test_manual_create_makes_folder_and_note(self):
        created = self.post(
            "/api/projects/create",
            {
                "customer": "Globex",
                "so": "so-55 , SO-56",
                "qte": "",
                "project_no": "0201234",
                "level": "l2",
                "description": "Liquid handler",
            },
        )
        self.assertEqual(created.status_code, 200)
        payload = created.get_json()
        folder = self.vault / "Globex SO-55 0201234"
        self.assertEqual(Path(payload["folder"]), folder)
        self.assertTrue(folder.is_dir())
        self.assertTrue((folder / "Globex SO-55 0201234.md").is_file())
        self.assertEqual(payload["key"], "0201234")
        self.assertTrue(payload["key_url"].endswith("/p/0201234"))
        self.assertTrue(self.runs)
        self.assertEqual([key for key in self.state()["pending"] if key.startswith("manual-")], [])

    def test_manual_create_requires_customer(self):
        response = self.post("/api/projects/create", {"customer": "  ", "so": "SO-1"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Customer is required", response.get_json()["error"])

    def test_manual_create_rejects_bad_project_no(self):
        response = self.post("/api/projects/create", {"customer": "Globex", "project_no": "12345"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("7 digits", response.get_json()["error"])

    def test_manual_create_existing_folder_400(self):
        response = self.post(
            "/api/projects/create",
            {"customer": "Acme", "folder_name": "Acme SO-1 0207000"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("already exists", response.get_json()["error"])

    def test_manual_create_custom_folder_name_no_numbers(self):
        created = self.post(
            "/api/projects/create",
            {"customer": "Initech", "folder_name": "Initech Pilot"},
        )
        self.assertEqual(created.status_code, 200)
        payload = created.get_json()
        folder = self.vault / "Initech Pilot"
        self.assertEqual(Path(payload["folder"]), folder)
        self.assertTrue(folder.is_dir())
        self.assertEqual(payload["key"], "Initech Pilot")

    def test_project_loading_page(self):
        (self.vault / "Beta SO-9 0207555").mkdir()
        response = self.client.get("/p/0207555", headers=HOST)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Setting up project", response.get_data(as_text=True))
        self.assertEqual(self.client.get("/p/0207999", headers=HOST).status_code, 404)

    def test_start_run_queueing(self):
        started = threading.Event()
        release = threading.Event()
        runs = []

        def runner() -> int:
            runs.append(1)
            started.set()
            release.wait(10)
            return 0

        app = create_app(self.config_path, port=8765, no_refresh=True, runner=runner)
        app.testing = True
        client = app.test_client()
        headers = dict(HOST, **{"X-Hub-Token": app.config["HUB_TOKEN"], "Origin": ORIGIN})
        first = client.post("/api/rerun", json={}, headers=headers)
        self.assertTrue(first.get_json()["started"])
        self.assertTrue(started.wait(5))
        second = client.post("/api/rerun", json={}, headers=headers)
        self.assertFalse(second.get_json()["started"])
        self.assertTrue(second.get_json()["running"])
        release.set()
        deadline = time.time() + 10
        while len(runs) < 2 and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(len(runs), 2)
        deadline = time.time() + 10
        while time.time() < deadline:
            if not client.get("/api/status", headers=HOST).get_json()["running"]:
                break
            time.sleep(0.05)
        self.assertFalse(client.get("/api/status", headers=HOST).get_json()["running"])

    def test_archive_and_reactivate_keep_the_folder(self):
        archived = self.post("/api/projects/archive", {"key": "0207000"})
        self.assertEqual(archived.status_code, 200, archived.get_json())
        self.assertTrue(self.folder.is_dir())
        self.assertFalse((self.vault / matcher.ARCHIVE_DIR).exists())
        entry = self.state()["archived_projects"]["0207000"]
        self.assertEqual(entry["rel"], "Acme SO-1 0207000")
        self.assertTrue(entry["at"])
        self.assertEqual(self.runs, [])
        self.assertEqual(self.post("/api/projects/archive", {"key": "0207000"}).status_code, 409)
        self.assertEqual(self.post("/api/projects/archive", {"key": "nope"}).status_code, 404)

        board = self.client.get("/?layout=board", headers=HOST).get_data(as_text=True)
        self.assertNotIn('class="card" data-key="0207000"', board)
        project = self.client.get("/p/0207000", headers=HOST).get_data(as_text=True)
        self.assertIn('data-action="project-unarchive" data-key="0207000"', project)
        self.assertIn("Archived on", project)
        listing = self.client.get("/projects", headers=HOST).get_data(as_text=True)
        self.assertIn('data-action="project-unarchive" data-key="0207000"', listing)

        reactivated = self.post("/api/projects/unarchive", {"key": "0207000"})
        self.assertEqual(reactivated.status_code, 200)
        self.assertEqual(self.state()["archived_projects"], {})
        self.assertEqual(len(self.runs), 1)
        self.assertEqual(self.post("/api/projects/unarchive", {"key": "0207000"}).status_code, 404)
        project = self.client.get("/p/0207000", headers=HOST).get_data(as_text=True)
        self.assertIn('data-action="project-archive" data-key="0207000"', project)

    def test_legacy_archive_folder_restore(self):
        legacy = self.vault / matcher.ARCHIVE_DIR / "Old SO-9"
        legacy.mkdir(parents=True)
        listing = self.client.get("/projects", headers=HOST).get_data(as_text=True)
        self.assertIn("Old _Archive folders", listing)
        restored = self.post("/api/projects/restore", {"name": "Old SO-9"})
        self.assertEqual(restored.status_code, 200)
        self.assertTrue((self.vault / "Old SO-9").is_dir())
        self.assertEqual(self.post("/api/projects/restore", {"name": "../x"}).status_code, 400)

    def test_delete_and_restore_deleted(self):
        self.post("/api/links/add", {"key": "0207000", "title": "CPR", "url": "https://example.com/cpr"})
        state = self.state()
        state["pending"]["msg-9"] = dict(PENDING, message_id="msg-9", suggested_folder="Acme SO-1 0207000")
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(
            self.post("/api/projects/delete", {"key": "0207000", "confirm": "wrong"}).status_code, 400
        )
        deleted = self.post("/api/projects/delete", {"key": "0207000", "confirm": "0207000"})
        self.assertEqual(deleted.status_code, 200)
        moved = Path(deleted.get_json()["moved_to"])
        self.assertEqual(moved.parent, self.backup_root / "deleted_projects")
        self.assertRegex(moved.name, r"^\d{8}_\d{6}_Acme SO-1 0207000$")
        self.assertTrue(moved.is_dir())
        self.assertFalse(self.folder.exists())
        self.assertEqual(self.state()["user_links"], {})
        self.assertEqual(self.state()["pending"]["msg-9"]["status"], "deleted")
        self.assertEqual(self.state()["pending"]["msg-1"]["status"], "new")

        restored = self.post("/api/projects/restore_deleted", {"name": moved.name})
        self.assertEqual(restored.status_code, 200)
        self.assertTrue(self.folder.is_dir())
        self.assertFalse(moved.exists())
        self.assertEqual(self.post("/api/projects/restore_deleted", {"name": moved.name}).status_code, 404)

    def test_projects_page_and_tokens(self):
        page = self.client.get("/projects", headers=HOST)
        self.assertEqual(page.status_code, 200)
        text = page.get_data(as_text=True)
        self.assertIn("Test the new-assignment workflow", text)
        self.assertIn("Simulate new assignment", text)
        self.assertIn("Acme SO-1 0207000", text)
        self.assertIn("Archived", text)
        self.assertIn("Deleted", text)
        self.assertIn(PENDING["subject"], text)

        for url in (
            "/api/pending/simulate",
            "/api/projects/archive",
            "/api/projects/unarchive",
            "/api/projects/delete",
            "/api/projects/restore",
            "/api/projects/restore_deleted",
            "/api/projects/open_folder",
        ):
            self.assertEqual(self.post(url, {}, token="wrong").status_code, 403, url)

    def test_favicon(self):
        response = self.client.get("/favicon.ico", headers=HOST)
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
