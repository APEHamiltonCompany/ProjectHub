from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tracker import matcher, notes
from tracker import projects as projects_mod
from tracker import tracker as tracker_mod
from tracker.hub.app import create_app
from tracker.models import Email, Project
from tracker.sources import ape_sheet
from tests.test_hub import HOST, ORIGIN

RULES = ROOT / "tracker" / "rules.yaml"
TODAY = date(2026, 11, 18)


def sheet_row(tab: str, ident: str, project_id: str = "", number: str = "", assigned: str = "2026-10-01") -> dict:
    return {
        "tab": tab,
        "so_status": "05 - Prints/BOM/Quotes",
        "assigned": assigned,
        "project_id": project_id,
        "customer": "Acme",
        "keys": {
            "so": [ident] if ident.startswith("SO-") else [],
            "qte": [ident] if ident.startswith("QTE-") else [],
            "project_no": [number] if number else [],
        },
    }


class FolderNameTests(unittest.TestCase):
    def test_customer_layout_keeps_qte_in_brackets(self):
        name = projects_mod.so_folder_name("QTE-054686", ["SO-285770"], matcher.LAYOUT_CUSTOMER)
        self.assertEqual(name, "SO-285770 (QTE-054686)")
        two = projects_mod.so_folder_name("QTE-056534, QTE-056535", ["SO-294732", "SO-294739"], matcher.LAYOUT_CUSTOMER)
        self.assertEqual(two, "SO-294732, SO-294739 (QTE-056534, QTE-056535)")

    def test_flat_layout_wraps_the_qte_part(self):
        name = projects_mod.so_folder_name("ADM QTE-054686 0207777", ["SO-285770"], matcher.LAYOUT_FLAT)
        self.assertEqual(name, "ADM SO-285770 (QTE-054686) 0207777")

    def test_no_rename_when_an_so_is_already_shown(self):
        self.assertEqual(projects_mod.so_folder_name("SO-223291 (QTE-042451)", ["SO-1"], matcher.LAYOUT_CUSTOMER), "")
        self.assertEqual(projects_mod.so_folder_name("QTE-054686", [], matcher.LAYOUT_CUSTOMER), "")


class SheetSuggestionTests(unittest.TestCase):
    def project(self, key, qte, so=(), number="", **extra):
        data = {"key": key, "name": key, "customer": "Acme", "qte": list(qte), "so": list(so), "project_no": number}
        data.update(extra)
        return data

    def suggest(self, rows, projects):
        return {(item["key"], item["so"]): item for item in ape_sheet.so_suggestions(rows, projects, TODAY)}

    def test_shared_project_id_is_ticked(self):
        rows = [sheet_row("New", "QTE-054686", "PRJ-008696"), sheet_row("Completed", "SO-285770", "PRJ-008696")]
        got = self.suggest(rows, [self.project("QTE-054686", ["QTE-054686"])])
        item = got[("QTE-054686", "SO-285770")]
        self.assertTrue(item["checked"])
        self.assertIn("PRJ-008696", item["reason"])

    def test_project_number_only_is_unticked_and_old_rows_ignored(self):
        rows = [
            sheet_row("Active", "SO-292921", number="0207703"),
            sheet_row("New", "SO-105915", number="0207703", assigned="2023-06-26"),
        ]
        got = self.suggest(rows, [self.project("0207703", ["QTE-056091"], number="0207703")])
        self.assertEqual(list(got), [("0207703", "SO-292921")])
        self.assertFalse(got[("0207703", "SO-292921")]["checked"])

    def test_so_with_its_own_folder_warns(self):
        rows = [sheet_row("New", "QTE-056193", "PRJ-008982"), sheet_row("Active", "SO-291520", "PRJ-008982")]
        projects = [self.project("QTE-056193", ["QTE-056193"]), self.project("SO-291520", [], so=["SO-291520"])]
        item = self.suggest(rows, projects)[("QTE-056193", "SO-291520")]
        self.assertFalse(item["checked"])
        self.assertIn("already has its own folder", item["note"])

    def test_known_dismissed_and_extra_sos(self):
        rows = [sheet_row("New", "QTE-1", "PRJ-1"), sheet_row("Active", "SO-200001", "PRJ-1"), sheet_row("Active", "SO-200002", "PRJ-1")]
        got = self.suggest(rows, [self.project("P", ["QTE-1"], so=["SO-200001"], so_dismissed=[])])
        self.assertEqual(list(got), [("P", "SO-200002")])
        self.assertFalse(got[("P", "SO-200002")]["checked"])
        self.assertEqual(self.suggest(rows, [self.project("P", ["QTE-1"], so_dismissed=["SO-200001", "SO-200002"])]), {})


class EmailHintTests(unittest.TestCase):
    def email(self, subject: str, day: int = 10) -> Email:
        return Email(
            message_id="m%d" % day, subject=subject, sender="pm@x.com", to="", cc="", body_text="", date=datetime(2026, 11, day, 9, tzinfo=timezone.utc)
        )

    def test_subject_with_project_qte_and_new_so(self):
        project = Project(folder="f", name="QTE-054686", qte=["QTE-054686"])
        other = Project(folder="g", name="SO-100001", so=["SO-100001"])
        emails = [
            self.email("WO Firm | ADM | QTE-054686 / SO-285770", 10),
            self.email("SO-999999 unrelated", 11),
            self.email("Status QTE-054686 SO-285770", 12),
        ]
        hints = tracker_mod.so_hints(emails, [project, other])
        self.assertEqual([hint["so"] for hint in hints["QTE-054686"]], ["SO-285770"])
        self.assertEqual(hints["QTE-054686"][0]["date"], "2026-11-12")
        self.assertNotIn("SO-100001", hints)

    def test_dismissed_and_known_sos_are_skipped(self):
        project = Project(folder="f", name="Q", qte=["QTE-054686"], so=["SO-285770"], so_dismissed=["SO-285771"])
        emails = [self.email("QTE-054686 SO-285770 SO-285771")]
        self.assertEqual(tracker_mod.so_hints(emails, [project]), {})


class StableTaskIdTests(unittest.TestCase):
    def test_id_prefix_survives_key_change(self):
        project = Project(folder="f", name="QTE-054686", qte=["QTE-054686"])
        self.assertEqual(tracker_mod.task_prefix(project), "pqte054686")
        project.so = ["SO-285770"]
        project.id_prefix = "pqte054686"
        self.assertEqual(project.key, "SO-285770")
        self.assertEqual(tracker_mod.task_prefix(project), "pqte054686")

    def test_add_so_writes_note_and_folder_reads_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "QTE-054686"
            folder.mkdir()
            note = folder / "QTE-054686.md"
            note.write_text("---\nqte: [QTE-054686]\nso_dismissed: [SO-285770, SO-1]\n---\n## Notes\nkeep me\n", encoding="utf-8")
            merged = projects_mod.add_so(note, ["SO-285770"], id_prefix="pqte054686")
            self.assertEqual(merged, ["SO-285770"])
            project = matcher.project_from_folder(folder)
            self.assertEqual((project.so, project.qte, project.id_prefix), (["SO-285770"], ["QTE-054686"], "pqte054686"))
            self.assertEqual(project.so_dismissed, [])
            self.assertIn("keep me", note.read_text(encoding="utf-8"))
            projects_mod.dismiss_so(note, "SO-300000")
            self.assertEqual(matcher.project_from_folder(folder).so_dismissed, ["SO-300000"])


class HubAddSoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        self.qte_folder = self.vault / "ADM" / "QTE-054686"
        self.qte_folder.mkdir(parents=True)
        self.note = self.qte_folder / "QTE-054686.md"
        self.note.write_text("---\ncustomer: ADM\nqte: [QTE-054686]\n---\n## Notes\n", encoding="utf-8")
        self.so_folder = self.vault / "Vertex" / "SO-291520"
        self.so_folder.mkdir(parents=True)
        self.snapshot_path = self.root / "snapshot.json"
        self.state_path = self.root / "state.json"
        cfg = {
            "vault": str(self.vault),
            "project_folder_layout": "customer/so",
            "state_path": str(self.state_path),
            "snapshot_path": str(self.snapshot_path),
            "log_path": str(self.root / "last_run.log"),
            "backup_root": str(self.root / "backups"),
            "templates_dir": str(self.root / "templates_out"),
            "rules_path": str(RULES),
            "me": {"name": "Test User"},
            "hub": {"port": 8765, "refresh_minutes": 15, "toast": False},
            "tools": {"wire_diagram_builder": ""},
        }
        config_path = self.root / "config.yaml"
        config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        self.state_path.write_text(
            json.dumps({"pending": {}, "projects": {"QTE-054686": {"kickoff": "2026-10-01"}},
                        "user_links": {"QTE-054686": [{"title": "SP", "url": "https://x"}]}}),
            encoding="utf-8",
        )
        hint = {"so": "SO-285770", "subject": "WO Firm QTE-054686 SO-285770", "date": "2026-11-12", "message_id": "m1"}
        projects = [
            self.project("QTE-054686", self.qte_folder, qte=["QTE-054686"], note_path=str(self.note), so_hints=[hint]),
            self.project("SO-291520", self.so_folder, so=["SO-291520"]),
        ]
        self.snapshot_path.write_text(json.dumps({
            "generated": "2026-11-18T06:30:00-08:00", "today": "2026-11-18",
            "projects": projects, "pending": [], "unmatched": [],
        }), encoding="utf-8")
        self.runs = []
        self.app = create_app(config_path, port=8765, no_refresh=True, runner=lambda: self.runs.append(1) or 0)
        self.app.testing = True

    def tearDown(self):
        self.tmp.cleanup()

    def project(self, key, folder, so=(), qte=(), note_path="", so_hints=()):
        return {
            "key": key, "name": folder.name, "folder": str(folder), "note_path": note_path, "customer": folder.parent.name,
            "project_no": "", "so": list(so), "qte": list(qte), "level": "", "stage": "Pre-Fabrication", "stage_manual": "",
            "assigned": "", "ship_date": "", "design_need_by": "", "sharepoint": "", "cpr": "", "rmt": "",
            "team": [], "links": [], "triggers": {}, "tasks": [], "emails": [], "so_hints": list(so_hints), "so_dismissed": [],
        }

    def post(self, url: str, payload: dict):
        headers = dict(HOST)
        headers["X-Hub-Token"] = self.app.config["HUB_TOKEN"]
        headers["Origin"] = ORIGIN
        return self.app.test_client().post(url, json=payload, headers=headers)

    def snap(self) -> dict:
        return {item["key"]: item for item in json.loads(self.snapshot_path.read_text(encoding="utf-8"))["projects"]}

    def test_project_page_shows_field_and_email_hint(self):
        html = self.app.test_client().get("/p/QTE-054686", headers=HOST).get_data(as_text=True)
        self.assertIn('id="so-input"', html)
        self.assertIn('id="so-rename" checked', html)
        self.assertIn("Add SO-285770", html)
        self.assertIn('data-action="so-dismiss"', html)

    def test_add_so_renames_folder_and_moves_state(self):
        response = self.post("/api/projects/so", {"key": "QTE-054686", "so": "so 285770", "rename": True})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        data = response.get_json()
        self.assertEqual((data["key"], data["added"], data["renamed"]), ("SO-285770", ["SO-285770"], "SO-285770 (QTE-054686)"))
        target = self.vault / "ADM" / "SO-285770 (QTE-054686)"
        self.assertFalse(self.qte_folder.exists())
        new_note = target / "SO-285770 (QTE-054686).md"
        self.assertTrue(new_note.is_file())
        project = matcher.project_from_folder(target)
        self.assertEqual((project.so, project.qte, project.id_prefix), (["SO-285770"], ["QTE-054686"], "pqte054686"))
        entry = self.snap()["SO-285770"]
        self.assertEqual((entry["folder"], entry["note_path"], entry["so_hints"]), (str(target), str(new_note), []))
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertIn("SO-285770", state["projects"])
        self.assertIn("SO-285770", state["user_links"])
        self.assertNotIn("QTE-054686", state["projects"])
        self.assertTrue(self.runs)

    def test_add_without_rename_and_owner_warning(self):
        response = self.post("/api/projects/so", {"key": "QTE-054686", "so": ["SO-291520"], "rename": False})
        data = response.get_json()
        self.assertEqual(data["renamed"], "")
        self.assertTrue(self.qte_folder.is_dir())
        self.assertIn("SO-291520 also has its own folder", data["warnings"][0])

    def test_bad_requests(self):
        self.assertEqual(self.post("/api/projects/so", {"key": "QTE-054686", "so": "nope"}).status_code, 400)
        self.assertEqual(self.post("/api/projects/so", {"key": "missing", "so": "SO-111111"}).status_code, 404)
        self.assertEqual(self.post("/api/projects/so", {"key": "SO-291520", "so": "SO-291520"}).status_code, 400)

    def test_dismiss_hides_hint(self):
        response = self.post("/api/projects/so/dismiss", {"key": "QTE-054686", "so": "SO-285770"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(matcher.project_from_folder(self.qte_folder).so_dismissed, ["SO-285770"])
        html = self.app.test_client().get("/p/QTE-054686", headers=HOST).get_data(as_text=True)
        self.assertNotIn("Add SO-285770", html)

    def test_sync_preview_and_apply_so_items(self):
        sheet = {"ok": True, "kind": "sharepoint", "source_mtime_label": "", "warning": "",
                 "rows": [sheet_row("New", "QTE-054686", "PRJ-008696"), sheet_row("Active", "SO-285770", "PRJ-008696")]}
        with mock.patch.object(ape_sheet, "load", return_value=sheet):
            data = self.post("/api/tracker-sync/preview", {}).get_json()
        items = data["so_suggestions"]
        self.assertEqual([(i["key"], i["so"], i["checked"], i["rename_to"]) for i in items],
                         [("QTE-054686", "SO-285770", True, "SO-285770 (QTE-054686)")])
        response = self.post("/api/tracker-sync/apply", {"items": [], "so_items": [{"key": "QTE-054686", "so": "SO-285770"}], "rename": True})
        result = response.get_json()
        self.assertEqual((result["so_added"], result["renamed"]), (1, 1), result)
        self.assertTrue((self.vault / "ADM" / "SO-285770 (QTE-054686)").is_dir())

    def test_sidebar_board_timeline_focus_show_customer_and_ident(self):
        from tracker.hub.app import project_ident, project_label

        self.assertEqual(project_ident({"so": ["SO-1"], "qte": ["QTE-2"]}), "SO-1 (QTE-2)")
        self.assertEqual(project_label({"customer": "ADM", "qte": ["QTE-054686"]}), "ADM \u00b7 QTE-054686")
        client = self.app.test_client()
        side = client.get("/settings", headers=HOST).get_data(as_text=True)
        self.assertIn('<span class="side-name">ADM</span><span class="side-ident">QTE-054686</span>', side)
        board = client.get("/?layout=board", headers=HOST).get_data(as_text=True)
        self.assertRegex(board, r'class="card-name"[^>]*>ADM</a>')
        self.assertIn('<div class="card-ident">QTE-054686</div>', board)
        timeline = client.get("/?layout=timeline", headers=HOST).get_data(as_text=True)
        self.assertRegex(timeline, r'>ADM</a></td>\s*<td class="nowrap"><a [^>]*>QTE-054686</a>')
        focus = client.get("/?layout=focus", headers=HOST).get_data(as_text=True)
        self.assertIn('<span class="side-name">Vertex</span><span class="side-ident">SO-291520</span>', focus)


if __name__ == "__main__":
    unittest.main()
