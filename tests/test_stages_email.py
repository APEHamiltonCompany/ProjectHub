from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tracker import matcher, notes, setup_gui
from tracker import projects as projects_mod
from tracker import rules as rules_mod
from tracker.hub.app import create_app
from tracker.models import Email
from tracker.sources import outlook_com
from tracker.tracker import collect_trigger_dates
from tests.test_hub import HOST, ORIGIN

RULES = ROOT / "tracker" / "rules.yaml"
ME = {"name": "Cristian Uribe", "email": "cristian.uribe@example.com"}
ASSIGNMENT = "Project Assignment - Acme | SO-123456 | 0209999 | STAR | L2"


def email(subject: str, to: str = "", cc: str = "", message_id: str = "m1") -> Email:
    return Email(
        message_id=message_id,
        subject=subject,
        sender="APE admin",
        to=to,
        cc=cc,
        date=datetime(2026, 11, 6, 10, 0, tzinfo=timezone.utc),
        body_text="",
    )


class AddressedToMeTests(unittest.TestCase):
    def test_to_line_matches(self):
        self.assertTrue(matcher.addressed_to_me("Jordan Poe; Cristian Uribe; Aman Aman", ME))
        self.assertTrue(matcher.addressed_to_me("Uribe, Cristian; Poe, Jordan", ME))
        self.assertTrue(matcher.addressed_to_me("cristian.uribe@example.com", ME))

    def test_not_on_to_line(self):
        self.assertFalse(matcher.addressed_to_me("Jordan Poe; Aman Aman", ME))
        self.assertFalse(matcher.addressed_to_me("Cristian Uribeson", ME))
        self.assertFalse(matcher.addressed_to_me("", ME))

    def test_unconfigured_is_unknown(self):
        self.assertIsNone(matcher.addressed_to_me("Anyone", {}))
        self.assertIsNone(matcher.addressed_to_me("Anyone", None))


class AssignmentToLineTests(unittest.TestCase):
    def run_collect(self, mail: Email, auto_create: bool = False):
        cfg = {"me": ME, "auto_create_projects": auto_create}
        rules = rules_mod.load_rules(RULES)
        return collect_trigger_dates(cfg, rules, [], matcher.build_index([]), [mail], {}, True)

    def test_cc_only_creates_nothing(self):
        _t, unmatched, pending, _m = self.run_collect(email(ASSIGNMENT, to="Jordan Poe", cc="Cristian Uribe"))
        self.assertEqual(pending, {})
        self.assertEqual(unmatched, [])

    def test_to_and_cc_pends(self):
        _t, _u, pending, _m = self.run_collect(email(ASSIGNMENT, to="Cristian Uribe; Jordan Poe", cc="Cristian Uribe"))
        self.assertIn("m1", pending)

    def test_to_only_pends(self):
        _t, _u, pending, _m = self.run_collect(email(ASSIGNMENT, to="Jordan Poe; Cristian Uribe"))
        self.assertIn("m1", pending)


class ClassifyTests(unittest.TestCase):
    def setUp(self):
        self.rules = rules_mod.load_rules(RULES)

    def stage(self, subject: str, rules: dict | None = None) -> str:
        trigger = rules_mod.classify(subject, rules or self.rules)
        return trigger.get("stage") if trigger else ""

    def test_buckets(self):
        self.assertEqual(rules_mod.bucket_order(self.rules)[:6],
                         ["Pre-Kickoff", "Pre-Fabrication", "Fabrication", "System Test", "Closeout", "Completion"])
        self.assertEqual(self.stage(ASSIGNMENT), "Pre-Kickoff")
        self.assertEqual(self.stage("Acme SO-123456 Kickoff Notification"), "Pre-Fabrication")
        self.assertEqual(self.stage("Design Review_Acme_SO-123456"), "Pre-Fabrication")
        self.assertEqual(self.stage("A&T Notification - SO-123456"), "System Test")
        self.assertEqual(self.stage("a & t notification: Vertex SO-123456"), "System Test")
        self.assertEqual(self.stage("Closeout SO-123456"), "Closeout")
        self.assertEqual(self.stage("Close out SO-123456"), "Closeout")

    def test_later_bucket_wins(self):
        self.assertEqual(self.stage("INTERNAL REVIEW APE WO FIRM SO-123456"), "Fabrication")

    def test_reply_to_assignment_ignored(self):
        self.assertEqual(self.stage("RE: " + ASSIGNMENT), "")
        self.assertTrue(rules_mod.is_reply("Automatic reply: A&T Notification"))

    def test_email_rules_from_config_override(self):
        cfg = {"email_rules": [{"id": "closeout", "label": "Done", "stage": "Completion", "keywords": "all finished"}]}
        rules = rules_mod.load_rules(RULES, cfg)
        self.assertEqual([t["id"] for t in rules["triggers"]], ["closeout"])
        self.assertEqual(self.stage("All  Finished SO-1", rules), "Completion")
        self.assertEqual(self.stage("Closeout SO-1", rules), "")


class NoteAndManualStageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        self.folder = self.vault / "Acme" / "SO-123456 (QTE-042451)"
        self.folder.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_note_and_never_overwrite(self):
        path, created = projects_mod.create_note_for_folder(self.folder, "Acme")
        self.assertTrue(created)
        self.assertEqual(path, self.folder / "SO-123456 (QTE-042451).md")
        data, _raw, _body = notes.split_frontmatter(path.read_text(encoding="utf-8"))
        self.assertEqual(data["customer"], "Acme")
        self.assertEqual(data["so"], ["SO-123456"])
        self.assertEqual(data["qte"], ["QTE-042451"])
        self.assertEqual(data["stage"], "Unknown")
        self.assertIn(b"\r\n", path.read_bytes())
        path.write_text("mine", encoding="utf-8")
        again, created = projects_mod.create_note_for_folder(self.folder, "Acme")
        self.assertFalse(created)
        self.assertEqual(again.read_text(encoding="utf-8"), "mine")

    def test_dry_run_writes_nothing(self):
        path, created = projects_mod.create_note_for_folder(self.folder, "Acme", dry_run=True)
        self.assertTrue(created)
        self.assertFalse(path.exists())

    def test_manual_stage_set_and_clear(self):
        path, _ = projects_mod.create_note_for_folder(self.folder, "Acme")
        projects_mod.set_manual_stage(path, "Fabrication")
        project = matcher.project_from_folder(self.folder, "Acme")
        self.assertEqual(project.stage_manual, "Fabrication")
        self.assertEqual(project.stage, "Fabrication")
        projects_mod.set_manual_stage(path, "")
        project = matcher.project_from_folder(self.folder, "Acme")
        self.assertEqual(project.stage_manual, "")
        self.assertEqual(project.stage, "Fabrication")


class StageApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        self.folders = {}
        for key in ("0201", "0202"):
            folder = self.vault / "Acme" / ("SO-%s" % key)
            folder.mkdir(parents=True)
            self.folders[key] = folder
        self.snapshot_path = self.root / "snapshot.json"
        cfg = {
            "vault": str(self.vault),
            "state_path": str(self.root / "state.json"),
            "snapshot_path": str(self.snapshot_path),
            "log_path": str(self.root / "last_run.log"),
            "backup_root": str(self.root / "backups"),
            "templates_dir": str(self.root / "templates_out"),
            "rules_path": str(RULES),
            "me": ME,
            "hub": {"port": 8765, "refresh_minutes": 15, "toast": False},
            "tools": {"wire_diagram_builder": ""},
        }
        config_path = self.root / "config.yaml"
        config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        (self.root / "state.json").write_text(json.dumps({"pending": {}}), encoding="utf-8")
        projects = [self.project(key) for key in self.folders]
        self.snapshot_path.write_text(json.dumps({
            "generated": "2026-11-10T06:30:00-08:00", "today": "2026-11-10",
            "projects": projects, "pending": [], "unmatched": [],
        }), encoding="utf-8")
        self.runs = []
        self.app = create_app(config_path, port=8765, no_refresh=True, runner=lambda: self.runs.append(1) or 0)
        self.app.testing = True

    def tearDown(self):
        self.tmp.cleanup()

    def project(self, key: str) -> dict:
        return {
            "key": "SO-%s" % key, "name": "SO-%s" % key, "folder": str(self.folders[key]), "note_path": "",
            "customer": "Acme", "project_no": "", "so": ["SO-%s" % key], "qte": [], "level": "",
            "stage": "Unknown", "stage_manual": "", "assigned": "", "ship_date": "", "design_need_by": "",
            "sharepoint": "", "cpr": "", "rmt": "", "team": [], "links": [], "triggers": {}, "tasks": [],
            "emails": [],
        }

    def post(self, payload: dict):
        headers = dict(HOST)
        headers["X-Hub-Token"] = self.app.config["HUB_TOKEN"]
        headers["Origin"] = ORIGIN
        return self.app.test_client().post("/api/projects/stage", json=payload, headers=headers)

    def snapshot_stage(self, key: str) -> tuple[str, str]:
        data = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        project = next(p for p in data["projects"] if p["key"] == key)
        return project["stage"], project["stage_manual"]

    def test_bulk_set_creates_notes(self):
        response = self.post({"keys": ["SO-0201", "SO-0202"], "stage": "Fabrication"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["updated"], 2)
        for key, folder in self.folders.items():
            note = folder / (folder.name + ".md")
            self.assertTrue(note.is_file())
            self.assertEqual(matcher.project_from_folder(folder, "Acme").stage_manual, "Fabrication")
            self.assertEqual(self.snapshot_stage("SO-%s" % key), ("Fabrication", "Fabrication"))

    def test_single_clear_triggers_refresh(self):
        self.post({"key": "SO-0201", "stage": "Closeout"})
        response = self.post({"key": "SO-0201", "stage": ""})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.snapshot_stage("SO-0201")[1], "")
        folder = self.folders["0201"]
        self.assertEqual(matcher.project_from_folder(folder, "Acme").stage_manual, "")

    def test_rejects_bad_input(self):
        self.assertEqual(self.post({"keys": ["SO-0201"], "stage": "Nonsense"}).status_code, 400)
        self.assertEqual(self.post({"keys": [], "stage": "Closeout"}).status_code, 400)
        response = self.post({"keys": ["nope"], "stage": "Closeout"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["updated"], 0)

    def test_task_add_creates_missing_note(self):
        headers = dict(HOST)
        headers["X-Hub-Token"] = self.app.config["HUB_TOKEN"]
        headers["Origin"] = ORIGIN
        response = self.app.test_client().post(
            "/api/tasks/add", json={"key": "SO-0202", "description": "Buy parts", "category": "project"}, headers=headers
        )
        self.assertEqual(response.status_code, 200)
        note = self.folders["0202"] / "SO-0202.md"
        self.assertIn("Buy parts", note.read_text(encoding="utf-8"))

    def test_pages_show_stage_controls(self):
        self.post({"key": "SO-0201", "stage": "System Test"})
        page = self.app.test_client().get("/projects", headers=HOST).get_data(as_text=True)
        self.assertIn('data-action="stage-bulk"', page)
        self.assertIn("Pre-Kickoff", page)
        self.assertIn("(manual)", page)
        page = self.app.test_client().get("/p/SO-0201", headers=HOST).get_data(as_text=True)
        self.assertIn('data-action="stage-set"', page)
        self.assertIn("set by hand", page)


class SetupEmailOptionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        with open(setup_gui.EXAMPLE_PATH, "r", encoding="utf-8") as fh:
            self.example = yaml.safe_load(fh)

    def tearDown(self):
        self.tmp.cleanup()

    def form(self, **changes):
        form = setup_gui.form_from_config(self.example)
        form.update(vault=str(self.vault), name="Test User", eml_folders=[], personal_tasks_path="")
        form.update(changes)
        return form

    def test_defaults_not_saved(self):
        form = self.form()
        self.assertEqual(form["outlook_scope"], "Whole mailbox")
        cfg, errors, _w = setup_gui.build_config(form, self.example)
        self.assertEqual(errors, [])
        self.assertEqual(cfg["outlook_com"]["scope"], "mailbox")
        self.assertNotIn("email_rules", cfg)

    def test_scope_skip_and_custom_rules(self):
        form = self.form(outlook_scope="Specific folders", skip_folders="Graymail, Inbox/IT")
        form["email_rules"][-1]["keywords"] = "closeout, wrap up"
        cfg, errors, _w = setup_gui.build_config(form, self.example)
        self.assertEqual(errors, [])
        self.assertEqual(cfg["outlook_com"]["scope"], "folders")
        self.assertEqual(cfg["outlook_com"]["skip_folders"], ["Graymail", "Inbox/IT"])
        self.assertEqual(cfg["email_rules"][-1]["keywords"], ["closeout", "wrap up"])

    def test_bad_bucket_is_error(self):
        form = self.form()
        form["email_rules"][0]["stage"] = "Nowhere"
        _cfg, errors, _w = setup_gui.build_config(form, self.example)
        self.assertTrue(any("choose a bucket" in e for e in errors))


class OutlookSkipTests(unittest.TestCase):
    def test_skipped(self):
        skip = ["Graymail", "Inbox/IT Announcements"]
        self.assertTrue(outlook_com._skipped("inbox/graymail", "Graymail", skip))
        self.assertTrue(outlook_com._skipped("Inbox/IT Announcements", "IT Announcements", skip))
        self.assertTrue(outlook_com._skipped("Inbox/IT Announcements/Old", "Old", skip))
        self.assertFalse(outlook_com._skipped("Inbox/Projects", "Projects", skip))
        self.assertEqual(outlook_com.scope({}), "mailbox")
        self.assertEqual(outlook_com.scope({"scope": "folders"}), "folders")


if __name__ == "__main__":
    unittest.main()
