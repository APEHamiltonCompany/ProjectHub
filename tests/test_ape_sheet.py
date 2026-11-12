from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tracker.models import Project
from tracker.sources import ape_sheet
from tracker.tracker import build_snapshot

ACTIVE_HEADERS = [
    "Customer",
    'Sales Order\n(or "Pre-\nSales")',
    "SK# & Project Page Link",
    "Project ID",
    "Instrument, Accessory, New vs. Upgrade",
    "Description ...",
    "Level/Tier, Internal Proj",
    "PM",
    "ME",
    "Suppt Staff (ME, AE, PM)",
    "AE",
    "APE Lab Build/Test Drives Instro Deliv to APE",
    "Sales Manager",
    "SO $Amount",
    "Order Entry Date",
    "Tracking Log Entry Date",
    "Assigned",
    "Baseline Delivery Date (DO NOT CHANGE)",
    "Ship Date\n(Initial=Baseline)\nOnly for SO/QTE",
    "SO Status",
    "Project Health",
    "Comments / History",
    "Instr Need-by Date or Delivery to-APE",
]
ACTIVE_ROW_MINE = [
    "Acme",
    "SO-111111",
    "0207001-001",
    "0207001",
    "Mirage",
    "Mirage and SpectraMax",
    "L2",
    "ST",
    "AZ",
    "AZ",
    "AZ",
    "Yes",
    "SM",
    12345,
    datetime(2026, 9, 1),
    datetime(2026, 9, 2),
    datetime(2026, 10, 1),
    datetime(2026, 11, 15),
    datetime(2026, 12, 1),
    "01 - Kickoff",
    "RISK",
    "line one\nline two",
    datetime(2026, 11, 20),
]
ACTIVE_ROW_OTHER = [
    "Beta",
    "SO-333333",
    "0207002-001",
    "0207002",
    "Spectra",
    "Other build",
    "L1",
    "ST",
    "MC",
    "MC",
    "MC",
    "No",
    "SM",
    999,
    datetime(2026, 9, 3),
    datetime(2026, 9, 4),
    datetime(2026, 10, 5),
    datetime(2026, 11, 16),
    datetime(2026, 12, 2),
    "06 - Fabrication",
    "OK",
    "",
    "NA",
]
NEW_HEADERS = [
    "Customer",
    'SO# ("Pre-sales")',
    "SK# & Project Page Link",
    "Project ID",
    "Instrument",
    "Description...",
    "Level",
    "PM",
    "APE Mech Eng",
    "Support Engr",
    "Automation Eng",
    "APE Lab Build/ Test",
    "Sales Manager",
    "SO $Amount (N/A for Pre-sales)",
    "Order Entry Date",
    "Tracking Log Entry Date",
    "CPR & SO Reviewed by ACE Date",
    "CPR Reviewed by TRT Date",
    "Eng. Assignment Date",
    "(Status) Not used here...",
    "(ProjHealth) Not used here...",
    "Comments / History",
]
NEW_ROW_MINE = [
    "Gamma",
    "SO-222222",
    "NA",
    "NA",
    "NA",
    "New integration",
    "L3",
    "ST",
    "AZ",
    "NA",
    "AZ",
    "NA",
    "NA",
    "NA",
    "NA",
    "NA",
    "NA",
    "NA",
    datetime(2026, 11, 1),
    "NA",
    "NA",
    "NA",
]
NEW_ROW_PLACEHOLDER = ["NA", "NA", "NA", "NA", "NA", "NA", "NA", "NA", "NA", "NA", "NA", "NA", "NA", "NA", "NA", "NA", "NA", "NA", "NA", "NA", "NA", "NA"]
NEW_ROW_OTHER = [
    "Delta",
    "SO-555555",
    "NA",
    "NA",
    "NA",
    "Another one",
    "NA",
    "ST",
    "MC",
    "NA",
    "MC",
    "NA",
    "NA",
    "NA",
    "NA",
    "NA",
    "NA",
    "NA",
    "NA",
    "NA",
    "NA",
    "NA",
]
DROPDOWN_ROWS = [
    ["STARlet23 (New)", "02 - Design", "-", "None", "AS", "Adriana Shurtz", "AZ", "AJ Zunino", "Alex Hantz"],
    ["STAR (New)", "03 - Design Review", "AA", "Aman Aman", "BL", "Brian LaMarre", "ST", "Scott Thompson"],
]


def build_workbook(path: Path) -> Path:
    wb = Workbook()
    active = wb.active
    active.title = "Active Projects"
    active.append(ACTIVE_HEADERS)
    active.append(ACTIVE_ROW_MINE)
    active.append(ACTIVE_ROW_OTHER)
    new = wb.create_sheet("New Projects")
    new.append(NEW_HEADERS)
    new.append(NEW_ROW_MINE)
    new.append(NEW_ROW_PLACEHOLDER)
    new.append(NEW_ROW_OTHER)
    dropdowns = wb.create_sheet("Dropdowns")
    for row in DROPDOWN_ROWS:
        dropdowns.append(row)
    wb.save(path)
    return path


def sheet_cfg(source: Path, copy_dir: Path) -> dict:
    return {
        "ape_sheet": {
            "enabled": True,
            "path": str(source),
            "pattern": "APE Project Tracking*.xlsx",
            "my_initials": "AZ",
            "stale_hours": 48,
            "copy_dir": str(copy_dir),
            "url": "https://example.invalid/sheet",
        }
    }


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ApeSheetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = build_workbook(self.root / "APE Project Tracking (2).xlsx")
        self.copy_dir = self.root / "copies"
        self.cfg = sheet_cfg(self.source, self.copy_dir)
        patcher = mock.patch.object(ape_sheet, "candidate_roots", lambda: [])
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_copies_and_parses(self):
        data = ape_sheet.load(self.cfg)
        self.assertTrue(data["ok"], data["error"])
        self.assertEqual(data["error"], "")
        self.assertEqual(Path(data["copy"]).parent, self.copy_dir)
        self.assertTrue(Path(data["copy"]).is_file())
        self.assertEqual(data["my_initials"], "AZ")
        self.assertEqual(data["url"], "https://example.invalid/sheet")
        self.assertFalse(data["stale"])
        rows = data["rows"]
        active = [row for row in rows if row["tab"] == "Active"]
        mine = active[0]
        self.assertEqual(mine["customer"], "Acme")
        self.assertEqual(mine["so"], "SO-111111")
        self.assertEqual(mine["ship_date"], "2026-12-01")
        self.assertEqual(mine["assigned"], "2026-10-01")
        self.assertEqual(mine["baseline"], "2026-11-15")
        self.assertEqual(mine["need_by"], "2026-11-20")
        self.assertEqual(mine["so_status"], "01 - Kickoff")
        self.assertEqual(mine["health"], "RISK")
        self.assertEqual(mine["pm_name"], "Scott Thompson")
        self.assertEqual(mine["ae_name"], "AJ Zunino")
        self.assertEqual(mine["me_name"], "AJ Zunino")
        self.assertEqual(mine["keys"]["project_no"], ["0207001"])
        self.assertEqual(mine["keys"]["so"], ["SO-111111"])
        for row in rows:
            for key in row:
                self.assertNotIn("$", key)
            self.assertNotIn(12345, row.values())
        new_rows = [row for row in rows if row["tab"] == "New"]
        self.assertEqual(len(new_rows), 2)
        placeholder = new_rows[1]
        self.assertEqual(placeholder["customer"], "Delta")
        self.assertEqual(placeholder["sk"], "")
        self.assertEqual(placeholder["project_id"], "")
        self.assertEqual(placeholder["level"], "")
        self.assertEqual(placeholder["ship_date"], "")
        self.assertEqual(placeholder["keys"]["so"], ["SO-555555"])
        self.assertEqual(data["names"]["AZ"], "AJ Zunino")
        self.assertEqual(data["names"]["ST"], "Scott Thompson")

    def test_source_untouched(self):
        before_hash = digest(self.source)
        before_stat = self.source.stat()
        data = ape_sheet.load(self.cfg)
        self.assertTrue(data["ok"], data["error"])
        after_stat = self.source.stat()
        self.assertEqual(digest(self.source), before_hash)
        self.assertEqual(after_stat.st_size, before_stat.st_size)
        self.assertEqual(after_stat.st_mtime, before_stat.st_mtime)

    def test_snapshot_copy_reuses_and_prunes(self):
        first = ape_sheet.snapshot_copy(self.source, self.copy_dir, keep=2)
        second = ape_sheet.snapshot_copy(self.source, self.copy_dir, keep=2)
        self.assertEqual(first, second)
        self.assertEqual(len(list(self.copy_dir.glob("APE_Tracking_*.xlsx"))), 1)
        for name in ("APE_Tracking_20200101-000000.xlsx", "APE_Tracking_20200102-000000.xlsx"):
            (self.copy_dir / name).write_bytes(b"old")
        ape_sheet.snapshot_copy(self.source, self.copy_dir, keep=2)
        names = sorted(entry.name for entry in self.copy_dir.glob("APE_Tracking_*.xlsx"))
        self.assertEqual(len(names), 2)
        self.assertEqual(names[-1], first.name)

    def test_resolve_source_newest_and_override(self):
        folder = self.root / "Downloads"
        folder.mkdir()
        old = build_workbook(folder / "APE Project Tracking.xlsx")
        stamp = datetime(2026, 1, 1, 8, 0).timestamp()
        os.utime(old, (stamp, stamp))
        newest = build_workbook(folder / "APE Project Tracking (2).xlsx")
        (folder / "~$APE Project Tracking (3).xlsx").write_bytes(b"lock")
        cfg = {"ape_sheet": {"path": str(folder), "pattern": "APE Project Tracking*.xlsx"}}
        self.assertEqual(ape_sheet.resolve_source(cfg), newest)
        override = dict(cfg)
        override["ape_sheet_path"] = str(old)
        self.assertEqual(ape_sheet.resolve_source(override), old)
        self.assertIsNone(ape_sheet.resolve_source({"ape_sheet": {"path": str(self.root / "missing")}}))

    def test_match_and_stage_mismatch(self):
        data = ape_sheet.load(self.cfg)
        project = Project(
            folder=str(self.root / "p1"),
            name="Acme SO-111111 0207001",
            project_no="0207001",
            so=["SO-111111"],
            stage="Design",
        )
        matches = ape_sheet.match(data["rows"], [project], "AZ")
        self.assertEqual(matches[project.key]["tab"], "Active")
        self.assertEqual(matches[project.key]["so_status"], "01 - Kickoff")
        self.assertTrue(ape_sheet.stage_mismatch("01 - Kickoff", "Design"))
        self.assertFalse(ape_sheet.stage_mismatch("06 - Fabrication", "Fabrication/Test"))
        self.assertFalse(ape_sheet.stage_mismatch("On-Going", "Design"))
        self.assertEqual(ape_sheet.stage_group("12 - Disassembly/Packing"), 4)
        self.assertIsNone(ape_sheet.stage_group("HOLD"))
        other_order = Project(
            folder=str(self.root / "p2"),
            name="Acme SO-999999 0207001",
            project_no="0207001",
            so=["SO-999999"],
            stage="Design",
        )
        self.assertNotIn(other_order.key, ape_sheet.match(data["rows"], [other_order], "AZ"))
        number_only = Project(folder=str(self.root / "p3"), name="Acme 0207001", project_no="0207001")
        self.assertIn(number_only.key, ape_sheet.match(data["rows"], [number_only], "AZ"))

    def test_sheet_pending(self):
        data = ape_sheet.load(self.cfg)
        project = Project(
            folder=str(self.root / "p1"),
            name="Acme SO-111111 0207001",
            project_no="0207001",
            so=["SO-111111"],
            stage="Design",
        )
        now = "2026-11-11T06:30:00-08:00"
        entries = ape_sheet.sheet_pending(data["rows"], "AZ", [project], {}, now)
        self.assertEqual(list(entries), ["sheet-SO-222222"])
        entry = entries["sheet-SO-222222"]
        self.assertEqual(entry["source"], "sheet")
        self.assertEqual(entry["status"], "new")
        self.assertEqual(entry["received"], now)
        self.assertEqual(entry["parsed"]["so"], ["SO-222222"])
        self.assertEqual(entry["parsed"]["level"], "L3")
        self.assertEqual(entry["suggested_folder"], "Gamma SO-222222")
        self.assertIn("Tracker sheet - Gamma | SO-222222", entry["subject"])
        dismissed = {"sheet-SO-222222": {"status": "dismissed", "parsed": {}}}
        self.assertEqual(ape_sheet.sheet_pending(data["rows"], "AZ", [project], dismissed, now), {})
        taken = {"other": {"status": "new", "parsed": {"so": ["SO-222222"]}}}
        self.assertEqual(ape_sheet.sheet_pending(data["rows"], "AZ", [project], taken, now), {})

    def test_load_missing_path(self):
        cfg = sheet_cfg(self.root / "missing.xlsx", self.copy_dir)
        data = ape_sheet.load(cfg)
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "no tracker sheet found")
        self.assertEqual(data["rows"], [])
        disabled = sheet_cfg(self.source, self.copy_dir)
        disabled["ape_sheet"]["enabled"] = False
        self.assertEqual(ape_sheet.load(disabled)["error"], "disabled")

    def test_build_snapshot_with_sheet(self):
        data = ape_sheet.load(self.cfg)
        project = Project(
            folder=str(self.root / "p1"),
            name="Acme SO-111111 0207001",
            project_no="0207001",
            so=["SO-111111"],
            stage="Design",
        )
        snapshot = build_snapshot(
            date(2026, 11, 11), [project], {}, {}, {}, {}, [], "2026-11-11T06:30:00-08:00", sheet=data
        )
        row = snapshot["projects"][0]
        self.assertEqual(row["sheet"]["tab"], "Active")
        self.assertTrue(row["sheet_mismatch"])
        self.assertEqual(row["ship_date"], "2026-12-01")
        self.assertEqual(row["ship_source"], "sheet")
        self.assertTrue(snapshot["ape_sheet"]["ok"])
        self.assertEqual(snapshot["ape_sheet"]["row_count"], len(data["rows"]))
        self.assertEqual(snapshot["ape_sheet"]["matched"], 1)
        self.assertNotIn("rows", snapshot["ape_sheet"])
        with_email = build_snapshot(
            date(2026, 11, 11),
            [project],
            {},
            {},
            {"0207001": {"ship_date": "2026-12-20"}},
            {},
            [],
            "gen",
            sheet=data,
        )
        self.assertEqual(with_email["projects"][0]["ship_date"], "2026-12-20")
        self.assertEqual(with_email["projects"][0]["ship_source"], "email")
        plain = build_snapshot(date(2026, 11, 11), [project], {}, {}, {}, {}, [], "gen")
        plain_row = plain["projects"][0]
        self.assertEqual(plain_row["sheet"], {})
        self.assertFalse(plain_row["sheet_mismatch"])
        self.assertEqual(plain_row["ship_date"], "")
        self.assertEqual(plain_row["ship_source"], "")
        self.assertFalse(plain["ape_sheet"]["ok"])


class SyncedSheetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.onedrive = self.root / "OneDrive - Hamilton Company"
        self.onedrive.mkdir()
        self.copy_dir = self.root / "copies"
        patcher = mock.patch.object(ape_sheet, "candidate_roots", lambda: [self.onedrive])
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def cfg(self, **extra) -> dict:
        sheet = {
            "enabled": True,
            "path": str(self.root / "Downloads"),
            "pattern": "APE Project Tracking*.xlsx",
            "my_initials": "AZ",
            "stale_hours": 48,
            "copy_dir": str(self.copy_dir),
        }
        sheet.update(extra)
        return {"ape_sheet": sheet}

    def synced(self) -> Path:
        folder = self.onedrive / "RNOAPE - General" / "Tracking Documents"
        folder.mkdir(parents=True)
        return build_workbook(folder / "APE Project Tracking.xlsx")

    def test_find_synced_finds_file(self):
        path = self.synced()
        self.assertEqual(ape_sheet.find_synced(self.cfg()), path)
        self.assertEqual(ape_sheet.resolve_info(self.cfg()), {"path": path, "kind": "synced"})
        self.assertEqual(ape_sheet.resolve_source(self.cfg()), path)

    def test_find_synced_folder_shortcut_name(self):
        folder = self.onedrive / "RNO-RD-APE - Tracking Documents"
        folder.mkdir(parents=True)
        path = build_workbook(folder / "APE Project Tracking.xlsx")
        self.assertEqual(ape_sheet.find_synced(self.cfg()), path)

    def test_downloads_fallback_when_nothing_synced(self):
        downloads = self.root / "Downloads"
        downloads.mkdir()
        source = build_workbook(downloads / "APE Project Tracking.xlsx")
        self.assertIsNone(ape_sheet.find_synced(self.cfg()))
        self.assertEqual(ape_sheet.resolve_info(self.cfg()), {"path": source, "kind": "downloads"})
        self.assertEqual(ape_sheet.resolve_source(self.cfg()), source)

    def test_kind_values(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        plain = build_workbook(elsewhere / "APE Project Tracking.xlsx")
        cfg = self.cfg(path=str(elsewhere), auto_find=False)
        self.assertEqual(ape_sheet.resolve_info(cfg), {"path": plain, "kind": "file"})
        override = self.cfg(auto_find=False)
        override["ape_sheet_path"] = str(plain)
        self.assertEqual(ape_sheet.resolve_info(override), {"path": plain, "kind": "override"})
        self.assertEqual(ape_sheet.resolve_info(self.cfg(path=str(self.root / "missing"))), {"path": None, "kind": ""})

    def test_shortcut_warning(self):
        (self.onedrive / "APE Project Tracking.xlsx.url").write_text(
            "URL=https://example.invalid/sheet.xlsx?web=1", encoding="utf-8"
        )
        warning = ape_sheet.shortcut_warning(self.cfg())
        self.assertIn("web shortcut", warning)
        self.assertIn("Tracking Documents", warning)
        self.synced()
        self.assertEqual(ape_sheet.shortcut_warning(self.cfg()), "")

    def test_synced_copy_is_never_stale(self):
        path = self.synced()
        stamp = datetime(2026, 1, 1, 8, 0).timestamp()
        os.utime(path, (stamp, stamp))
        data = ape_sheet.load(self.cfg())
        self.assertTrue(data["ok"], data["error"])
        self.assertEqual(data["kind"], "synced")
        self.assertFalse(data["stale"])
        self.assertEqual(data["source_mtime_label"], "01 Jan 08:00")
        self.assertEqual(data["warning"], "")

    def test_downloads_copy_goes_stale(self):
        downloads = self.root / "Downloads"
        downloads.mkdir()
        path = build_workbook(downloads / "APE Project Tracking.xlsx")
        stamp = (datetime.now() - timedelta(days=3)).timestamp()
        os.utime(path, (stamp, stamp))
        data = ape_sheet.load(self.cfg())
        self.assertTrue(data["ok"], data["error"])
        self.assertEqual(data["kind"], "downloads")
        self.assertTrue(data["stale"])


if __name__ == "__main__":
    unittest.main()
