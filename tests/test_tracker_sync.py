from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

import yaml
from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tracker import matcher
from tracker import projects as projects_mod
from tracker.hub.app import create_app
from tracker.sources import ape_sheet, outlook_com
from tests.test_hub import HOST, ORIGIN

RULES = ROOT / "tracker" / "rules.yaml"
ORDER = ["Pre-Kickoff", "Pre-Fabrication", "Fabrication", "System Test", "Closeout", "Completion"]
TODAY = date(2026, 11, 17)


def row(tab: str, so: str, status: str = "", assigned: str = "", ae: str = "") -> dict:
    return {
        "tab": tab,
        "so_status": status,
        "assigned": assigned,
        "ae": ae,
        "customer": "Acme",
        "description": "",
        "level": "",
        "keys": {
            "so": [so] if so.startswith("SO-") else [],
            "qte": [so] if so.startswith("QTE-") else [],
            "project_no": [so] if so.isdigit() else [],
        },
    }


def project(key: str, so: list[str], stage: str = "Unknown", **extra) -> dict:
    data = {"key": key, "name": key, "customer": "Acme", "so": so, "qte": [], "project_no": "", "stage": stage,
            "stage_manual": "", "assigned": "", "last_email": ""}
    data.update(extra)
    return data


def propose(rows, projects):
    return {item["key"]: item for item in ape_sheet.sync_proposals(rows, projects, TODAY, ORDER)}


class StatusMapTests(unittest.TestCase):
    def test_status_stage(self):
        self.assertEqual(ape_sheet.status_stage("01 - Kickoff"), "Pre-Kickoff")
        self.assertEqual(ape_sheet.status_stage("05 - Prints/BOM/Quotes"), "Pre-Fabrication")
        self.assertEqual(ape_sheet.status_stage("06 - Fabrication"), "Fabrication")
        self.assertEqual(ape_sheet.status_stage("11 - x"), "Fabrication")
        self.assertEqual(ape_sheet.status_stage("12 - Disassembly/Packing"), "System Test")
        self.assertEqual(ape_sheet.status_stage("13 - Ship Partial"), "Completion")
        self.assertEqual(ape_sheet.status_stage("HOLD"), "")
        self.assertTrue(ape_sheet.is_ongoing("On-Going"))
        self.assertTrue(ape_sheet.is_hold("HOLD"))


class SyncProposalTests(unittest.TestCase):
    def test_single_so_cases(self):
        rows = [
            row("Active", "SO-1", "13 - Inventory, SO-full"),
            row("Completed", "SO-2"),
            row("Active", "SO-3", "HOLD"),
            row("Active", "SO-4", "On-Going"),
            row("Active", "SO-5", "12 - Disassembly/Packing"),
            row("Active", "SO-6", "06 - Fabrication"),
        ]
        projects = [project("P%d" % n, ["SO-%d" % n]) for n in range(1, 8)]
        out = propose(rows, projects)
        self.assertEqual(out["P1"]["proposed"], "Completion")
        self.assertEqual(out["P2"]["proposed"], "Completion")
        self.assertNotIn("P3", out)
        self.assertNotIn("P4", out)
        self.assertEqual(out["P5"]["proposed"], "System Test")
        self.assertEqual(out["P6"]["proposed"], "Fabrication")
        self.assertEqual(out["P7"]["proposed"], "Completion")
        self.assertIn("not on Active Projects", out["P7"]["reason"])
        self.assertTrue(all(item["checked"] for item in out.values()))

    def test_multiple_sos(self):
        rows = [row("Completed", "SO-1"), row("Completed", "SO-2"), row("Active", "SO-3", "05 - Prints"),
                row("Active", "SO-4", "01 - Kickoff"), row("Active", "SO-5", "HOLD")]
        out = propose(rows, [
            project("ALL_DONE", ["SO-1", "SO-2"]),
            project("MIXED", ["SO-1", "SO-3", "SO-4"]),
            project("HELD", ["SO-1", "SO-5"]),
        ])
        self.assertEqual(out["ALL_DONE"]["proposed"], "Completion")
        self.assertEqual(out["MIXED"]["proposed"], "Pre-Kickoff")
        self.assertIn("SO-3: 05 - Prints", out["MIXED"]["reason"])
        self.assertNotIn("HELD", out)

    def test_safety_and_skips(self):
        rows = [row("Active", "SO-2", "02 - Design"), row("Active", "SO-3", "06 - Fabrication"),
                row("New", "SO-6", assigned="2026-11-01")]
        out = propose(rows, [
            project("RECENT", ["SO-1"], "Pre-Kickoff", assigned="2026-11-10"),
            project("SAME", ["SO-2"], "Pre-Fabrication"),
            project("LAGGING", ["SO-3"], "System Test"),
            project("HAND", ["SO-1"], "Closeout", stage_manual="Closeout"),
            {"key": "QUOTE", "name": "QUOTE", "customer": "", "so": [], "qte": ["QTE-9"], "project_no": "", "stage": "Unknown"},
            project("NEW", ["SO-6"]),
        ])
        self.assertFalse(out["RECENT"]["checked"])
        self.assertIn("recent activity", out["RECENT"]["note"])
        self.assertNotIn("SAME", out)
        self.assertFalse(out["LAGGING"]["checked"])
        self.assertEqual(out["LAGGING"]["proposed"], "Fabrication")
        self.assertFalse(out["HAND"]["checked"])
        self.assertEqual(out["HAND"]["note"], "stage set by hand")
        self.assertNotIn("QUOTE", out)
        self.assertNotIn("NEW", out)

    def test_previous_sync_is_not_by_hand(self):
        rows = [row("Active", "SO-1", "12 - Packing")]
        out = propose(rows, [project("P", ["SO-1"], "Fabrication", stage_manual="Fabrication",
                                     stage_manual_source=ape_sheet.SYNC_SOURCE)])
        self.assertTrue(out["P"]["checked"])
        self.assertEqual(out["P"]["proposed"], "System Test")


class SheetPendingTests(unittest.TestCase):
    def test_only_active_and_recent_new_rows(self):
        rows = [
            row("New", "SO-1", assigned="2023-01-05", ae="CU"),
            row("New", "SO-2", assigned="2026-11-10", ae="CU"),
            row("Completed", "SO-3", ae="CU"),
            row("Active", "SO-4", "On-Going", ae="CU"),
            row("Active", "SO-5", "02 - Design", ae="CU"),
            row("New", "SO-6", assigned="2026-11-10", ae="CU"),
            row("Completed", "SO-6"),
        ]
        out = ape_sheet.sheet_pending(rows, "CU", [], {}, "2026-11-17T08:00:00-08:00")
        self.assertEqual(sorted(out), ["sheet-SO-2", "sheet-SO-5"])


class SharePointSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.shortcut = self.root / "APE Project Tracking.xlsx.url"
        self.shortcut.write_text("[InternetShortcut]\nURL=https://example.sharepoint.com/x/APE.xlsx?web=1\n", encoding="utf-8")
        self.copies = self.root / "copies"
        self.copies.mkdir()
        self.cfg = {"ape_sheet": {"shortcut": str(self.root), "copy_dir": str(self.copies), "my_initials": "CU"}}

    def tearDown(self):
        self.tmp.cleanup()

    def workbook(self, name: str) -> Path:
        wb = Workbook()
        ws = wb.active
        ws.title = "Active Projects"
        ws.append(["Customer", "Sales Order #", "SO Status"])
        ws.append(["Acme", "SO-111111", "06 - Fabrication"])
        done = wb.create_sheet("Completed FY2026")
        done.append(["Customer", "Sales Order #"])
        done.append(["Beta", "SO-222222"])
        path = self.copies / name
        wb.save(path)
        return path

    def test_resolves_shortcut(self):
        self.assertEqual(ape_sheet.shortcut_url(self.shortcut), "https://example.sharepoint.com/x/APE.xlsx?web=1")
        info = ape_sheet.resolve_info(self.cfg)
        self.assertEqual(info["kind"], ape_sheet.SHAREPOINT)
        self.assertEqual(info["path"], self.shortcut)

    def test_fresh_copy_is_used_without_download(self):
        self.workbook("APE_Tracking_20261117-080000.xlsx")
        with mock.patch.object(ape_sheet, "download_copy", side_effect=AssertionError("no download")):
            sheet = ape_sheet.load(self.cfg)
        self.assertTrue(sheet["ok"], sheet["error"])
        self.assertEqual(sorted(r["tab"] for r in sheet["rows"]), ["Active", "Completed"])

    def test_forced_download_and_fallback(self):
        old = self.workbook("APE_Tracking_20261110-080000.xlsx")
        fresh = self.root / "fresh.xlsx"
        old.replace(fresh)
        target = self.copies / "APE_Tracking_20261117-090000.xlsx"

        def fake(url, copy_dir, keep=5):
            self.assertEqual(url, "https://example.sharepoint.com/x/APE.xlsx?web=1")
            fresh.replace(target)
            return target

        with mock.patch.object(ape_sheet, "download_copy", side_effect=fake):
            sheet = ape_sheet.load(self.cfg, download=True)
        self.assertTrue(sheet["ok"], sheet["error"])
        self.assertEqual(sheet["copy"], str(target))
        with mock.patch.object(ape_sheet, "download_copy", side_effect=RuntimeError("signed out")):
            sheet = ape_sheet.load(self.cfg, download=True)
        self.assertTrue(sheet["ok"])
        self.assertIn("signed out", sheet["warning"])

    def test_download_failure_without_copy(self):
        with mock.patch.object(ape_sheet, "download_copy", side_effect=RuntimeError("no excel")):
            sheet = ape_sheet.load(self.cfg)
        self.assertFalse(sheet["ok"])
        self.assertIn("no excel", sheet["error"])


class IncrementalReadTests(unittest.TestCase):
    def test_since(self):
        now = datetime(2026, 11, 17, 12, 0)
        key = outlook_com._scope_key({"scope": "mailbox"}, 120)
        meta = {"scope": key, "last_read": "2026-11-17T11:00:00", "last_full": "2026-11-17T06:00:00"}
        self.assertEqual(outlook_com.incremental_since(meta, key, now, {}), datetime(2026, 11, 16, 11, 0))
        self.assertIsNone(outlook_com.incremental_since({}, key, now, {}))
        self.assertIsNone(outlook_com.incremental_since(meta, key + "x", now, {}))
        self.assertIsNone(outlook_com.incremental_since(meta, key, now, {"incremental": False}))
        stale = dict(meta, last_full="2026-11-16T06:00:00")
        self.assertIsNone(outlook_com.incremental_since(stale, key, now, {}))


class ManualSourceTests(unittest.TestCase):
    def test_source_set_and_cleared(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "Acme" / "SO-123456"
            folder.mkdir(parents=True)
            path, _ = projects_mod.create_note_for_folder(folder, "Acme")
            projects_mod.set_manual_stage(path, "Completion", ape_sheet.SYNC_SOURCE)
            found = matcher.project_from_folder(folder, "Acme")
            self.assertEqual(found.stage_manual_source, ape_sheet.SYNC_SOURCE)
            projects_mod.set_manual_stage(path, "Closeout")
            self.assertEqual(matcher.project_from_folder(folder, "Acme").stage_manual_source, "")
            projects_mod.set_manual_stage(path, "")
            self.assertNotIn("stage_manual", path.read_text(encoding="utf-8"))


class MappedDriveTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_disconnected_drive_uses_unc(self):
        from tracker import settings as settings_mod

        class FakeKey:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with mock.patch("os.path.exists", side_effect=lambda p: False), mock.patch(
            "winreg.OpenKey", return_value=FakeKey()
        ), mock.patch("winreg.QueryValueEx", return_value=("\\\\srv\\Share", 1)):
            cfg = settings_mod.resolve_drives({"vault": "Q:/APE_AE/My Projects", "n": 3, "l": ["Q:\\T"], "u": "https://x"})
        self.assertEqual(cfg["vault"], "\\\\srv\\Share\\APE_AE\\My Projects")
        self.assertEqual(cfg["l"], ["\\\\srv\\Share\\T"])
        self.assertEqual((cfg["n"], cfg["u"]), (3, "https://x"))

    def test_connected_or_unmapped_drive_unchanged(self):
        from tracker import settings as settings_mod

        self.assertEqual(settings_mod.unc_path("C:/Windows"), "")
        with mock.patch("os.path.exists", return_value=False), mock.patch("winreg.OpenKey", side_effect=OSError):
            self.assertEqual(settings_mod.unc_path("Q:/nothing"), "")


class HubSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        self.folders = {}
        for so in ("SO-100001", "SO-100002", "SO-100003"):
            folder = self.vault / "Acme" / so
            folder.mkdir(parents=True)
            self.folders[so] = folder
        self.snapshot_path = self.root / "snapshot.json"
        cfg = {
            "vault": str(self.vault),
            "state_path": str(self.root / "state.json"),
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
        (self.root / "state.json").write_text(json.dumps({"pending": {}}), encoding="utf-8")
        stages = {"SO-100001": "Unknown", "SO-100002": "Fabrication", "SO-100003": "Completion"}
        projects = [self.project(so, stages[so]) for so in self.folders]
        self.snapshot_path.write_text(json.dumps({
            "generated": "2026-11-17T06:30:00-08:00", "today": "2026-11-17",
            "projects": projects, "pending": [], "unmatched": [],
        }), encoding="utf-8")
        self.app = create_app(config_path, port=8765, no_refresh=True, runner=lambda: 0)
        self.app.testing = True

    def tearDown(self):
        self.tmp.cleanup()

    def project(self, so: str, stage: str) -> dict:
        return {
            "key": so, "name": so, "folder": str(self.folders[so]), "note_path": "", "customer": "Acme",
            "project_no": "", "so": [so], "qte": [], "level": "", "stage": stage, "stage_manual": "",
            "assigned": "", "ship_date": "", "design_need_by": "", "sharepoint": "", "cpr": "", "rmt": "",
            "team": [], "links": [], "triggers": {},
            "tasks": [{"id": "t1", "title": "Task for " + so, "stage": "Closeout", "due": "2026-11-01",
                       "status": "open", "mark": " ", "source": "tracker"}],
            "emails": [],
        }

    def post(self, url: str, payload: dict):
        headers = dict(HOST)
        headers["X-Hub-Token"] = self.app.config["HUB_TOKEN"]
        headers["Origin"] = ORIGIN
        return self.app.test_client().post(url, json=payload, headers=headers)

    def test_preview_and_apply(self):
        sheet = {"ok": True, "kind": "sharepoint", "source_mtime_label": "17 Nov 09:00", "warning": "",
                 "rows": [row("Completed", "SO-100001"), row("Active", "SO-100002", "12 - Packing")]}
        with mock.patch.object(ape_sheet, "load", return_value=sheet) as loader:
            response = self.post("/api/tracker-sync/preview", {})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(loader.call_args.kwargs.get("download"), True)
        data = response.get_json()
        got = {item["key"]: item["proposed"] for item in data["proposals"]}
        self.assertEqual(got, {"SO-100001": "Completion", "SO-100002": "System Test"})
        self.assertEqual(data["sheet"]["tabs"], {"Completed": 1, "Active": 1})
        response = self.post("/api/tracker-sync/apply", {"items": [{"key": k, "stage": v} for k, v in got.items()]})
        self.assertEqual(response.get_json()["updated"], 2)
        found = matcher.project_from_folder(self.folders["SO-100001"], "Acme")
        self.assertEqual((found.stage_manual, found.stage_manual_source), ("Completion", ape_sheet.SYNC_SOURCE))
        snap = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        self.assertEqual({p["key"]: p["stage"] for p in snap["projects"]}["SO-100002"], "System Test")

    def test_preview_error_and_bad_apply(self):
        with mock.patch.object(ape_sheet, "load", return_value={"ok": False, "error": "no excel"}):
            response = self.post("/api/tracker-sync/preview", {})
        self.assertEqual(response.status_code, 502)
        self.assertIn("no excel", response.get_json()["error"])
        self.assertEqual(self.post("/api/tracker-sync/apply", {"items": []}).status_code, 400)
        self.assertEqual(self.post("/api/tracker-sync/apply", {"items": [{"key": "SO-100001", "stage": "Nope"}]}).status_code, 400)

    def test_completion_collapsed_on_board(self):
        page = self.app.test_client().get("/?layout=board", headers=HOST).get_data(as_text=True)
        self.assertIn("is-collapsed", page)
        self.assertIn("1 completed project: see Projects", page)
        self.assertNotIn("Task for SO-100003", page)
        self.assertIn("Task for SO-100002", page)
        page = self.app.test_client().get("/projects?stage=Completion", headers=HOST).get_data(as_text=True)
        self.assertIn('<option value="Completion" selected>', page)
        self.assertIn('data-action="sync-preview"', page)


if __name__ == "__main__":
    unittest.main()
