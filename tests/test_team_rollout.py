from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tracker import importer, matcher
from tracker import rules as rules_mod
from tracker import tracker as tracker_mod
from tracker.hub.app import create_app
from tracker.models import Email, Project
from tests.test_hub import HOST, ORIGIN, TEMPLATE_TEXT

RULES = ROOT / "tracker" / "rules.yaml"
ME = {"name": "Cristian Uribe", "email": "cristian.uribe@example.com"}


def mail(subject: str, to: str = "Cristian Uribe", message_id: str = "m1", day: int = 6, entry_id: str = "") -> Email:
    return Email(
        message_id=message_id,
        subject=subject,
        sender="APE admin",
        to=to,
        cc="",
        date=datetime(2026, 11, day, 10, 0, tzinfo=timezone.utc),
        body_text="",
        entry_id=entry_id,
    )


class CreationBlockTests(unittest.TestCase):
    def projects(self, count: int) -> list[Project]:
        return [Project(folder="f%d" % i, name="P SO-%06d" % i, so=["SO-%06d" % i]) for i in range(count)]

    def test_reasons(self):
        on = {"auto_create_projects": True, "me": ME}
        self.assertEqual(tracker_mod.creation_block({}, self.projects(3), 3), "switched off")
        self.assertIn("name and email", tracker_mod.creation_block({"auto_create_projects": True}, self.projects(3), 3))
        self.assertIn("backup", tracker_mod.creation_block(dict(on, offline={"active": True}), self.projects(3), 3))
        self.assertIn("no projects", tracker_mod.creation_block(on, [], 4))
        self.assertIn("found 7", tracker_mod.creation_block(on, self.projects(7), 10))
        self.assertEqual(tracker_mod.creation_block(on, self.projects(8), 10), "")
        self.assertEqual(tracker_mod.creation_block(on, [], 0), "")


class AutoCreateRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.templates = self.root / "templates"
        self.templates.mkdir()
        (self.templates / "Project Template.md").write_text(TEMPLATE_TEXT, encoding="utf-8")
        existing = self.vault / "Beta SO-100002"
        existing.mkdir()
        (existing / (existing.name + ".md")).write_text("---\nlevel: L1\n---\n", encoding="utf-8")
        self.state_path = self.root / "state.json"
        self.snapshot_path = self.root / "snapshot.json"
        self.config = self.root / "config.yaml"
        self.cfg = {
            "vault": str(self.vault),
            "state_path": str(self.state_path),
            "snapshot_path": str(self.snapshot_path),
            "log_path": str(self.root / "last_run.log"),
            "templates_dir": str(self.templates),
            "rules_path": str(RULES),
            "me": ME,
            "ape_sheet": {"enabled": False},
            "auto_create_projects": True,
        }
        self.config.write_text(yaml.safe_dump(self.cfg), encoding="utf-8")
        self.emails = [mail("Project Assignment - Acme | SO-100001 | 0209999 | Widget | L2")]

    def tearDown(self):
        self.tmp.cleanup()

    def run_tracker(self) -> dict:
        with mock.patch.object(tracker_mod, "collect_all", return_value=self.emails):
            self.assertEqual(tracker_mod.main(["--config", str(self.config), "--no-dashboard"]), 0)
        return json.loads(self.snapshot_path.read_text(encoding="utf-8"))

    def state(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def folders(self) -> list[str]:
        return sorted(p.name for p in self.vault.rglob("*") if p.is_dir())

    def test_creates_once_and_lists_it(self):
        snap = self.run_tracker()
        created = self.state()["auto_created"]
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["message_id"], "m1")
        self.assertIn(created[0]["key"], {row["key"] for row in snap["projects"]})
        self.assertEqual(self.state()["pending"], {})
        before = self.folders()
        self.run_tracker()
        self.assertEqual(self.folders(), before)
        self.assertEqual(len(self.state()["auto_created"]), 1)

    def test_cc_only_is_not_created(self):
        self.emails = [mail("Project Assignment - Acme | SO-100001 | 0209999 | Widget | L2", to="Jordan Poe")]
        self.run_tracker()
        self.assertEqual(self.folders(), ["Beta SO-100002"])
        self.assertEqual(self.state().get("auto_created"), [])

    def test_shrunken_scan_only_pends(self):
        rows = [{"key": "SO-9%05d" % i, "name": "X SO-9%05d" % i} for i in range(10)]
        self.snapshot_path.write_text(json.dumps({"projects": rows}), encoding="utf-8")
        snap = self.run_tracker()
        self.assertEqual(self.folders(), ["Beta SO-100002"])
        self.assertIn("m1", self.state()["pending"])
        self.assertIn("found 1", snap["auto_create"]["blocked"])

    def test_off_by_default(self):
        self.config.write_text(yaml.safe_dump(dict(self.cfg, auto_create_projects=False)), encoding="utf-8")
        snap = self.run_tracker()
        self.assertEqual(self.folders(), ["Beta SO-100002"])
        self.assertIn("m1", self.state()["pending"])
        self.assertEqual(snap["auto_create"], {"enabled": False, "blocked": ""})


class LearnKeysTests(unittest.TestCase):
    def setUp(self):
        self.rules = rules_mod.load_rules(RULES)

    def test_learns_so_for_020_only_folder(self):
        projects = [Project(folder="a", name="Acme_0207001", project_no="0207001")]
        learned: dict = {}
        added = tracker_mod.learn_keys(
            projects, [mail("Project Assignment - Acme | SO-300001 / QTE-400001 | 0207001 | X | L1")], self.rules, learned
        )
        self.assertEqual(projects[0].so, ["SO-300001"])
        self.assertEqual(projects[0].qte, ["QTE-400001"])
        self.assertEqual(learned, {"0207001": {"so": ["SO-300001"], "qte": ["QTE-400001"]}})
        self.assertEqual(len(added), 2)
        fresh = [Project(folder="a", name="Acme_0207001", project_no="0207001")]
        tracker_mod.apply_learned_keys(fresh, learned)
        self.assertEqual(fresh[0].so, ["SO-300001"])

    def test_shared_020_and_owned_numbers_teach_nothing(self):
        projects = [
            Project(folder="a", name="A_0207054", project_no="0207054"),
            Project(folder="b", name="B_0207054", project_no="0207054"),
            Project(folder="c", name="C_0207002", project_no="0207002"),
            Project(folder="d", name="D SO-300009", so=["SO-300009"]),
        ]
        learned: dict = {}
        tracker_mod.learn_keys(
            projects,
            [
                mail("Project Assignment - A | SO-300005 | 0207054 | X | L1"),
                mail("Project Assignment - C | SO-300009 | 0207002 | X | L1", message_id="m2"),
                mail("RE: Project Assignment - C | SO-300010 | 0207002 | X | L1", message_id="m3"),
            ],
            self.rules,
            learned,
        )
        self.assertEqual(learned, {})
        self.assertEqual([p.so for p in projects[:3]], [[], [], []])

    def test_number_conflicts(self):
        projects = [
            Project(folder="/v/Natera/SO-277011", name="SO-277011", so=["SO-277011"]),
            Project(folder="/v/Neo/SO-277011", name="SO-277011", so=["SO-277011"]),
            Project(folder="/v/Solo SO-1", name="Solo SO-1", so=["SO-1"]),
        ]
        found = tracker_mod.number_conflicts(projects, "/v")
        self.assertEqual(found, [{"number": "SO-277011", "folders": ["Natera/SO-277011", "Neo/SO-277011"]}])


class ImporterPreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        (self.vault / "Exists SO-500003").mkdir()
        self.rules = rules_mod.load_rules(RULES)
        self.cfg = {"vault": str(self.vault), "me": ME, "project_folder_layout": "flat"}
        self.known = [Project(folder=str(self.vault / "Old SO-500009"), name="Old SO-500009", so=["SO-500009"])]

    def tearDown(self):
        self.tmp.cleanup()

    def preview(self, emails, sheet_rows=None):
        return {row["key"]: row for row in importer.build_preview(self.cfg, self.rules, self.known, emails, sheet_rows)}

    def test_rows(self):
        emails = [
            mail("Project Assignment - Acme | SO-500001 | 0208001 | X | L2", message_id="a", day=2),
            mail("Acme SO-500001 Kickoff Notification", message_id="b", day=4),
            mail("Project Assignment - Acme | SO-500001 / SO-500002 | 0208001 | X | L2", message_id="c", day=5),
            mail("Project Assignment - Cc | SO-500004 | 0208004 | X | L2", to="Jordan Poe", message_id="d"),
            mail("Project Assignment - Old | SO-500009 | 0208009 | X | L2", message_id="e"),
            mail("Project Assignment - Exists | SO-500003 | | X | L2", message_id="f"),
            mail("Project Assignment - Done | SO-500006 | 0208006 | X | L2", message_id="g"),
        ]
        sheet = [{"keys": {"so": ["SO-500006"], "qte": [], "project_no": []}, "tab": "Completed", "status": "13 - Closed"}]
        rows = self.preview(emails, sheet)
        self.assertEqual(sorted(rows), ["0208001", "0208006", "SO-500003"])
        acme = rows["0208001"]
        self.assertEqual(acme["so"], ["SO-500001", "SO-500002"])
        self.assertEqual(acme["triggers"]["assignment"], "2026-11-02")
        self.assertEqual(acme["stage"], "Pre-Fabrication")
        self.assertTrue(acme["checked"])
        self.assertTrue(rows["0208006"]["completed"])
        self.assertFalse(rows["0208006"]["checked"])
        self.assertTrue(rows["SO-500003"]["folder_exists"])
        self.assertFalse(rows["SO-500003"]["checked"])


class HubRolloutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.templates = self.root / "templates"
        self.templates.mkdir()
        (self.templates / "Project Template.md").write_text(TEMPLATE_TEXT, encoding="utf-8")
        self.folders = []
        for name in ("Acme SO-100001", "Beta SO-100002", "Gamma SO-100003"):
            folder = self.vault / name
            folder.mkdir()
            (folder / (name + ".md")).write_text("---\nlevel: L1\n---\n", encoding="utf-8")
            self.folders.append(folder)
        self.state_path = self.root / "state.json"
        self.snapshot_path = self.root / "snapshot.json"
        self.config_path = self.root / "config.yaml"
        cfg = {
            "vault": str(self.vault),
            "state_path": str(self.state_path),
            "snapshot_path": str(self.snapshot_path),
            "log_path": str(self.root / "last_run.log"),
            "templates_dir": str(self.templates),
            "rules_path": str(RULES),
            "project_folder_layout": "flat",
            "me": ME,
            "hub": {"port": 8765, "refresh_minutes": 15, "toast": False},
        }
        self.config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        self.state_path.write_text(json.dumps({
            "auto_created": [{"key": "SO-100003", "folder": "Gamma SO-100003", "subject": "Project Assignment - Gamma", "message_id": "x", "at": "2026-11-23T08:00:00"}],
        }), encoding="utf-8")
        projects = [
            {"key": f.name.split()[-1], "name": f.name, "folder": str(f), "customer": f.name.split()[0], "so": [f.name.split()[-1]], "qte": [], "project_no": "", "tasks": [], "emails": []}
            for f in self.folders
        ]
        self.snapshot_path.write_text(json.dumps({
            "generated": "2026-11-23T08:00:00", "projects": projects, "pending": [], "unmatched": [],
            "conflicts": [{"number": "SO-100001", "folders": ["Acme SO-100001", "Other SO-100001"]}],
        }), encoding="utf-8")
        self.runs = []
        self.app = create_app(self.config_path, port=8765, no_refresh=True, runner=lambda: self.runs.append(1) or 0)
        self.app.testing = True
        self.client = self.app.test_client()
        self.token = self.app.config["HUB_TOKEN"]

    def tearDown(self):
        self.tmp.cleanup()

    def post(self, url: str, payload: dict):
        return self.client.post(url, json=payload, headers=dict(HOST, **{"X-Hub-Token": self.token, "Origin": ORIGIN}))

    def state(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def wait_import(self) -> dict:
        for _ in range(200):
            data = self.client.get("/api/import/status", headers=HOST).get_json()
            if not data["running"]:
                return data
            time.sleep(0.05)
        self.fail("import did not finish")

    def test_archive_selected(self):
        response = self.post("/api/projects/archive", {"keys": ["SO-100001", "SO-100002", "nope"]})
        data = response.get_json()
        self.assertEqual(data["archived"], 2)
        self.assertEqual(len(data["failed"]), 1)
        self.assertEqual(sorted(self.state()["archived_projects"]), ["SO-100001", "SO-100002"])
        self.assertTrue(all(f.is_dir() for f in self.folders))
        again = self.post("/api/projects/archive", {"keys": ["SO-100001"]}).get_json()
        self.assertEqual(again["archived"], 0)

    def test_projects_page_shows_conflicts_and_import(self):
        page = self.client.get("/projects", headers=HOST).get_data(as_text=True)
        self.assertIn("Numbers used by more than one folder", page)
        self.assertIn("Other SO-100001", page)
        self.assertIn('data-action="archive-selected"', page)
        self.assertIn('data-action="import-scan"', page)

    def test_dashboard_lists_auto_created_and_dismiss(self):
        page = self.client.get("/", headers=HOST).get_data(as_text=True)
        self.assertIn("Created automatically", page)
        self.assertIn("Gamma SO-100003", page)
        self.assertEqual(self.post("/api/auto-created/dismiss", {"key": "SO-100003"}).get_json()["dismissed"], 1)
        page = self.client.get("/", headers=HOST).get_data(as_text=True)
        self.assertNotIn("Created automatically", page)

    def test_settings_toggle(self):
        self.assertEqual(self.post("/api/settings", {"auto_create_projects": True}).status_code, 200)
        overlay = json.loads((self.root / "settings.json").read_text(encoding="utf-8"))
        self.assertIs(overlay["auto_create_projects"], True)
        self.assertEqual(self.post("/api/settings", {"auto_create_projects": "yes"}).status_code, 400)

    def test_import_scan_and_create(self):
        emails = [
            mail("Project Assignment - Delta | SO-100004 | 0208004 | X | L2", message_id="d1", day=2, entry_id="E1"),
            mail("Delta SO-100004 Kickoff Notification", message_id="d2", day=9),
            mail("Project Assignment - Acme | SO-100001 | 0208001 | X | L2", message_id="a1"),
        ]
        with mock.patch("tracker.sources.outlook_com.collect_headers", return_value=emails) as headers, \
                mock.patch("tracker.sources.outlook_com.item_body", return_value="") as body:
            self.assertEqual(self.post("/api/import/scan", {"days": 400, "include_archive": True}).status_code, 200)
            data = self.wait_import()
            self.assertEqual(data["error"], "")
            self.assertEqual(headers.call_args[0][1:3], (400, True))
            self.assertEqual([row["key"] for row in data["rows"]], ["0208004"])
            self.assertNotIn("entry_id", data["rows"][0])
            self.assertEqual(self.post("/api/import/create", {"ids": ["d1"]}).status_code, 200)
            data = self.wait_import()
            body.assert_called_once_with("E1")
        self.assertEqual(data["result"]["created"], 1, data)
        created = [p for p in self.vault.iterdir() if "SO-100004" in p.name]
        self.assertEqual(len(created), 1)
        state = self.state()
        key = state["imported"][0]["key"]
        self.assertEqual(state["projects"][key]["assignment"], "2026-11-02")
        self.assertEqual(state["projects"][key]["kickoff"], "2026-11-09")
        self.assertEqual(data["rows"], [])
        self.assertTrue(self.runs)


if __name__ == "__main__":
    unittest.main()
