from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock
from xml.sax.saxutils import escape

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tracker import extract
from tracker import notes
from tracker import projects as projects_mod
from tracker import rules as rules_mod
from tracker import state as state_mod
from tracker.hub.app import create_app
from tracker.models import Project, Task
from tracker.sources import ape_sheet, ms_project
from tracker.tracker import apply_schedule_done, build_tasks, milestone_rows, pick_schedule

RULES = rules_mod.load_rules(ROOT / "tracker" / "rules.yaml")
TODAY = date(2026, 11, 20)
NOW = "2026-11-20T10:00:00-08:00"
ME = {"name": "Anthony Zunino", "email": "anthony.zunino@hamiltoncompany.com"}
PREFIXES = ["TRT |"]
HOST = {"Host": "127.0.0.1:8765"}
ORIGIN = "http://127.0.0.1:8765"
HUB_NOTE = """---
project_no: "0207000"
customer: "Acme"
stage: "Pre-Fabrication"
---

**TO DO:**
"""

TASKS = [
    (0, "APE Group Schedule", 0, 1, "", "", 0, ""),
    (1, "[L2 TEMPLATE] Customer - Description - PM", 1, 1, "2026-01-05T08:00:00", "2026-01-30T17:00:00", 0, ""),
    (2, "Cust - SO PreFabApps", 2, 0, "2026-01-05T08:00:00", "2026-01-09T17:00:00", 0, ""),
    (3, "Cust - SO Testing", 2, 0, "2026-01-12T08:00:00", "2026-01-16T17:00:00", 0, ""),
    (4, "[NEW PROJECT SCHEDULING]", 1, 1, "2022-02-07T08:00:00", "2026-11-20T17:00:00", 80, ""),
    (5, "Assigned | Acme - SO-111111 - Widget Integration", 2, 0, "2026-11-02T08:00:00", "2026-11-02T17:00:00", 100, ""),
    (6, "Assigned | Acme - SO-111111 - Widget Integration Follow Up", 2, 0, "2026-11-06T08:00:00", "2026-11-06T17:00:00", 100, ""),
    (7, "Assigned | Beta - SO-222222, SO-222223 - Other Thing", 2, 0, "2026-11-05T08:00:00", "2026-11-05T17:00:00", 100, ""),
    (8, "Assigned | Delta - QTE-047606 - Table", 2, 0, "2026-11-09T08:00:00", "2026-11-09T17:00:00", 100, ""),
    (9, "Assigned | Old - SO-333333 - Done", 2, 0, "2026-06-01T08:00:00", "2026-06-01T17:00:00", 100, ""),
    (10, "[TRT REQUESTS]", 1, 1, "2023-01-01T08:00:00", "2026-11-24T17:00:00", 92, ""),
    (11, "TRT | Gamma - ACE ASAP - Study", 2, 0, "2026-11-02T08:00:00", "2026-12-18T17:00:00", 0, ""),
    (12, "External Lab Space Requests", 1, 1, "2026-09-28T08:00:00", "2026-12-01T17:00:00", 0, ""),
    (13, "Marketing", 2, 0, "", "", 0, ""),
    (14, "Acme - Widget Integration - CH", 1, 1, "2026-11-03T08:00:00", "2027-02-12T17:00:00", 40, ""),
    (15, "Acme - SO-111111  PreFabApps", 2, 0, "2026-11-04T08:00:00", "2026-12-04T17:00:00", 100, ""),
    (16, "Acme - SO-111111  Send Test Plan for Review & List of Parts for Testing", 2, 0, "2026-11-25T08:00:00", "2026-11-30T17:00:00", 0, ""),
    (17, "Acme - SO-111111 -  Assembly", 2, 0, "2026-12-07T08:00:00", "2026-12-11T17:00:00", 0, ""),
    (18, "Acme - SO-111111 PBS Project Review", 2, 1, "2026-05-28T08:00:00", "2026-05-28T17:00:00", 0, ""),
    (19, "Acme - SO-111111 Check Lead Times for Vendor Quotes", 3, 0, "2026-12-03T08:00:00", "2026-12-03T17:00:00", 0, ""),
    (20, "Acme - SO-111111 Testing", 2, 0, "2027-01-04T08:00:00", "2027-01-29T17:00:00", 0, ""),
    (21, "Acme - SO-111111 AE  Pack Up", 2, 0, "2027-02-01T08:00:00", "2027-02-03T17:00:00", 0, ""),
    (22, "Acme - SO-111111 AutoE Close Out", 2, 0, "2027-02-04T08:00:00", "2027-02-12T17:00:00", 0, ""),
    (23, "Beta - Other Thing - CD", 1, 1, "2026-11-05T08:00:00", "2027-03-05T17:00:00", 10, ""),
    (24, "Beta - SO-222222 PreFabApps", 2, 0, "2026-11-05T08:00:00", "2026-12-11T17:00:00", 10, ""),
    (25, "Beta - SO-222223 Testing", 2, 0, "2027-01-04T08:00:00", "2027-01-29T17:00:00", 0, ""),
    (26, "Delta - Table - AA", 1, 1, "2026-11-09T08:00:00", "2027-01-15T17:00:00", 0, ""),
    (27, "Delta - QTE-047606 PreFabApps", 2, 0, "2026-11-09T08:00:00", "2026-12-18T17:00:00", 0, ""),
    (28, "Old - Done - CH", 1, 1, "2026-06-01T08:00:00", "2026-07-31T17:00:00", 100, ""),
    (29, "Old - SO-333333 Testing", 2, 0, "2026-06-01T08:00:00", "2026-07-10T17:00:00", 100, ""),
    (30, "Old - SO-333333 AutoE Close Out", 2, 0, "2026-07-13T08:00:00", "2026-07-31T17:00:00", 100, ""),
    (31, "Moderna - RHS Workcell - MS", 1, 1, "2026-08-07T08:00:00", "2026-11-24T17:00:00", 83, ""),
    (32, "Moderna SO-281090  PreFabApps", 2, 0, "2026-08-10T08:00:00", "2026-09-04T17:00:00", 100, ""),
    (33, "Moderna SO-281090  Testing", 2, 0, "2026-10-05T08:00:00", "2026-10-30T17:00:00", 0, ""),
    (34, "Corteva - New STAR - VerSO-291675-STAR Integration - CD (AUTOLOAD CARRIER REWORK)", 1, 1, "2026-05-11T08:00:00", "2026-10-23T17:00:00", 99, ""),
    (35, "Corteva - SO-291675 PreFabApps", 2, 0, "2026-05-11T08:00:00", "2026-06-12T17:00:00", 100, ""),
    (36, "Three Rivers Dx - New 12CH 2.0 STAR V; Adapter, Reagent Trough, Pin (VCAP) - CD (L1R)", 1, 1, "2026-06-16T08:00:00", "2026-09-23T17:00:00", 99, ""),
    (37, "Three Rivers Dx - SO-666666 Testing", 2, 0, "2026-08-03T08:00:00", "2026-08-28T17:00:00", 100, ""),
    (38, "Kickoff Only - Thing - ST", 1, 1, "2026-10-01T08:00:00", "2026-12-18T17:00:00", 20, ""),
    (39, "Kickoff Only - SO-444444 Kick-Off", 2, 0, "2026-09-21T08:00:00", "2026-09-21T17:00:00", 100, ""),
    (40, "Kickoff Only - SO-444444 Testing", 2, 0, "2026-11-02T08:00:00", "2026-11-27T17:00:00", 0, ""),
    (41, "Summary Only - Thing - DM", 1, 1, "2026-08-03T08:00:00", "2026-10-30T17:00:00", 30, ""),
    (42, "Summary Only - SO-555555 Testing", 2, 0, "2026-08-31T08:00:00", "2026-09-25T17:00:00", 0, ""),
]
RESOURCES = [
    (1, "Anthony Zunino", ""),
    (2, "Needs Auto Eng", ""),
    (3, "Someone Else", "someone.else@hamiltoncompany.com"),
]
ASSIGNMENTS = [(5, 1), (6, 1), (7, 3), (8, 1), (9, 1), (15, 1), (16, 1), (20, 2), (21, 1), (22, 1), (24, 3), (27, 1), (29, 1), (30, 1), (33, 1), (35, 3), (37, 1), (40, 1), (42, 1)]
SHEET_ROW = {
    "tab": "Active",
    "row": 2,
    "customer": "Acme",
    "so": "SO-999999",
    "qte": "",
    "project_no": "",
    "description": "Thing",
    "level": "L2",
    "ae": "AZ",
    "so_status": "01 - Kickoff",
    "assigned": "2026-11-01",
    "keys": {"so": ["SO-999999"], "qte": [], "project_no": []},
}


def mspdi_text() -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Project xmlns="http://schemas.microsoft.com/project">',
        "  <Tasks>",
    ]
    for uid, name, level, summary, start, finish, pct, created in TASKS:
        lines.append("    <Task>")
        lines.append("      <UID>%d</UID>" % uid)
        lines.append("      <Name>%s</Name>" % escape(name))
        lines.append("      <OutlineLevel>%d</OutlineLevel>" % level)
        lines.append("      <Summary>%d</Summary>" % summary)
        if start:
            lines.append("      <Start>%s</Start>" % start)
        if finish:
            lines.append("      <Finish>%s</Finish>" % finish)
        lines.append("      <PercentComplete>%d</PercentComplete>" % pct)
        if created:
            lines.append("      <CreateDate>%s</CreateDate>" % created)
        lines.append("    </Task>")
    lines.append("  </Tasks>")
    lines.append("  <Resources>")
    for uid, name, email in RESOURCES:
        lines.append("    <Resource>")
        lines.append("      <UID>%d</UID>" % uid)
        lines.append("      <Name>%s</Name>" % escape(name))
        if email:
            lines.append("      <EmailAddress>%s</EmailAddress>" % email)
        lines.append("    </Resource>")
    lines.append("  </Resources>")
    lines.append("  <Assignments>")
    for task_uid, resource_uid in ASSIGNMENTS:
        lines.append("    <Assignment>")
        lines.append("      <TaskUID>%d</TaskUID>" % task_uid)
        lines.append("      <ResourceUID>%d</ResourceUID>" % resource_uid)
        lines.append("    </Assignment>")
    lines.append("  </Assignments>")
    lines.append("</Project>")
    return "\n".join(lines) + "\n"


def write_fixture(folder, name: str = "schedule.xml") -> Path:
    path = Path(folder) / name
    path.write_text(mspdi_text(), encoding="utf-8")
    return path


def by_customer(projects: list[dict], customer: str) -> dict:
    return next(item for item in projects if item["customer"] == customer)


def schedule_project(uid: str, kind: str, so: list[str], people: list[dict], finish: str) -> dict:
    return {
        "uid": uid,
        "name": "x",
        "kind": kind,
        "customer": "Acme",
        "so": list(so),
        "qte": [],
        "description": "",
        "created": "",
        "start": "",
        "finish": finish,
        "people": list(people),
        "rows": [],
    }


class MspdiReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raw = ms_project.read_mspdi(write_fixture(self.tmp.name))

    def test_tasks_and_resources(self):
        self.assertEqual(len(self.raw["tasks"]), len(TASKS) - 1)
        self.assertEqual([item["id"] for item in self.raw["resources"]], ["1", "2", "3"])
        self.assertEqual(self.raw["resources"][0]["email"], "")
        self.assertEqual(self.raw["resources"][2]["email"], "someone.else@hamiltoncompany.com")

    def test_task_fields(self):
        task = next(item for item in self.raw["tasks"] if item["uid"] == "15")
        self.assertEqual(task["name"], "Acme - SO-111111  PreFabApps")
        self.assertEqual(task["outline_level"], 2)
        self.assertFalse(task["summary"])
        self.assertEqual(task["start"], "2026-11-04")
        self.assertEqual(task["finish"], "2026-12-04")
        self.assertEqual(task["pct"], 100)
        self.assertEqual(task["resource_ids"], ["1"])

    def test_summary_flag_and_created(self):
        task = next(item for item in self.raw["tasks"] if item["uid"] == "14")
        self.assertTrue(task["summary"])
        self.assertEqual(task["created"], "")
        self.assertEqual(task["pct"], 40)

    def test_project_summary_skipped(self):
        self.assertNotIn("0", [item["uid"] for item in self.raw["tasks"]])

    def test_assignments_attached(self):
        task = next(item for item in self.raw["tasks"] if item["uid"] == "20")
        self.assertEqual(task["resource_ids"], ["2"])


class NormaliseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raw = ms_project.read_mspdi(write_fixture(self.tmp.name))

    def test_projects_from_summaries(self):
        projects = ms_project.normalise(self.raw)
        self.assertEqual(
            [item["customer"] for item in projects],
            ["Acme", "Beta", "Delta", "Old", "Moderna", "Corteva", "Three Rivers Dx", "Kickoff Only", "Summary Only"],
        )
        self.assertEqual({item["kind"] for item in projects}, {"assigned"})

    def test_include_trt(self):
        projects = ms_project.normalise(self.raw, PREFIXES)
        self.assertEqual(len(projects), 10)
        gamma = by_customer(projects, "Gamma")
        self.assertEqual(gamma["kind"], "trt")
        self.assertEqual(gamma["rows"], [])

    def test_template_is_not_a_project(self):
        names = [item["name"] for item in ms_project.normalise(self.raw, PREFIXES)]
        self.assertFalse(any("TEMPLATE" in name for name in names))
        self.assertFalse(any(name.startswith("[") for name in names))

    def test_skip_list(self):
        names = [item["name"] for item in ms_project.normalise(self.raw, skip=[])]
        self.assertIn("[NEW PROJECT SCHEDULING]", names)
        self.assertNotIn("[TRT REQUESTS]", names)
        custom = [item["customer"] for item in ms_project.normalise(self.raw, skip=["[", "External Lab Space Requests", "Acme"])]
        self.assertNotIn("Acme", custom)
        self.assertIn("Beta", custom)

    def test_name_parsing(self):
        projects = ms_project.normalise(self.raw, PREFIXES)
        acme = by_customer(projects, "Acme")
        self.assertEqual(acme["so"], ["SO-111111"])
        self.assertEqual(acme["qte"], [])
        self.assertEqual(acme["description"], "Widget Integration")
        self.assertEqual(acme["pm"], "CH")
        self.assertEqual(acme["level"], "")
        self.assertEqual(acme["created"], "2026-11-02")
        self.assertEqual(acme["finish"], "2027-02-12")
        beta = by_customer(projects, "Beta")
        self.assertEqual(beta["so"], ["SO-222222", "SO-222223"])
        self.assertEqual(beta["description"], "Other Thing")
        self.assertEqual(beta["pm"], "CD")
        delta = by_customer(projects, "Delta")
        self.assertEqual(delta["so"], [])
        self.assertEqual(delta["qte"], ["QTE-047606"])
        self.assertEqual(delta["description"], "Table")
        self.assertEqual(delta["pm"], "AA")

    def test_pm_and_level(self):
        projects = ms_project.normalise(self.raw)
        rivers = by_customer(projects, "Three Rivers Dx")
        self.assertEqual(rivers["pm"], "CD (L1R)")
        self.assertEqual(rivers["level"], "L1R")
        self.assertEqual(rivers["description"], "New 12CH 2.0 STAR V; Adapter, Reagent Trough, Pin (VCAP)")
        self.assertEqual(rivers["so"], ["SO-666666"])
        corteva = by_customer(projects, "Corteva")
        self.assertEqual(corteva["pm"], "CD (AUTOLOAD CARRIER REWORK)")
        self.assertEqual(corteva["level"], "")
        self.assertEqual(corteva["description"], "New STAR - VerSO-291675-STAR Integration")

    def test_orders_ignore_verso(self):
        corteva = by_customer(ms_project.normalise(self.raw), "Corteva")
        self.assertEqual(corteva["so"], ["SO-291675"])
        self.assertNotIn("SO-291675-STAR", corteva["so"])

    def test_created_precedence(self):
        projects = ms_project.normalise(self.raw)
        self.assertEqual(by_customer(projects, "Acme")["created"], "2026-11-02")
        self.assertEqual(by_customer(projects, "Kickoff Only")["created"], "2026-09-21")
        self.assertEqual(by_customer(projects, "Summary Only")["created"], "2026-08-03")
        self.assertEqual(by_customer(projects, "Moderna")["created"], "2026-08-07")

    def test_assignments(self):
        rows = ms_project.assignment_rows(self.raw)
        self.assertEqual([row["uid"] for row in rows], ["5", "6", "7", "8", "9"])
        self.assertEqual(rows[0]["customer"], "Acme")
        self.assertEqual(rows[0]["so"], ["SO-111111"])
        self.assertEqual(rows[0]["description"], "Widget Integration")
        self.assertEqual(rows[0]["date"], "2026-11-02")
        self.assertEqual(rows[0]["people"], [{"name": "Anthony Zunino", "email": ""}])
        self.assertEqual(rows[2]["so"], ["SO-222222", "SO-222223"])
        self.assertEqual(rows[3]["qte"], ["QTE-047606"])

    def test_rows_and_people(self):
        acme = by_customer(ms_project.normalise(self.raw, PREFIXES), "Acme")
        self.assertEqual(
            [row["name"] for row in acme["rows"]],
            [
                "Acme - SO-111111  PreFabApps",
                "Acme - SO-111111  Send Test Plan for Review & List of Parts for Testing",
                "Acme - SO-111111 -  Assembly",
                "Acme - SO-111111 Check Lead Times for Vendor Quotes",
                "Acme - SO-111111 Testing",
                "Acme - SO-111111 AE  Pack Up",
                "Acme - SO-111111 AutoE Close Out",
            ],
        )
        self.assertEqual(acme["rows"][0]["finish"], "2026-12-04")
        self.assertEqual(acme["rows"][0]["pct"], 100)
        self.assertEqual(acme["rows"][0]["people"], [{"name": "Anthony Zunino", "email": ""}])
        self.assertEqual([person["name"] for person in acme["people"]], ["Anthony Zunino", "Needs Auto Eng"])

    def test_rows_are_not_summaries(self):
        acme = by_customer(ms_project.normalise(self.raw, PREFIXES), "Acme")
        self.assertEqual(len(acme["rows"]), 7)
        self.assertFalse(any("PBS Project Review" in row["name"] for row in acme["rows"]))


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.source = write_fixture(self.folder)
        self.cfg = {
            "me": dict(ME),
            "ms_project": {
                "path": str(self.source),
                "cache_path": str(self.folder / "cache.json"),
                "copy_dir": str(self.folder / "copies"),
            },
        }

    def counting_reader(self):
        calls = []

        def reader(path, cfg):
            calls.append(str(path))
            return ms_project.read_mspdi(path)

        return calls, reader

    def test_caching(self):
        calls, reader = self.counting_reader()
        first = ms_project.load(self.cfg, reader=reader)
        self.assertTrue(first["ok"], first.get("error"))
        self.assertEqual(first["reader"], "mspdi")
        self.assertEqual(len(first["projects"]), 9)
        self.assertEqual(len(first["assignments"]), 5)
        self.assertEqual(len(calls), 1)
        self.assertTrue(Path(first["copy"]).is_file())
        self.assertEqual(Path(first["copy"]).suffix, ".xml")
        second = ms_project.load(self.cfg, reader=reader)
        self.assertEqual(second["reader"], "cache")
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(second["projects"]), 9)
        self.assertEqual(len(second["assignments"]), 5)

    def test_changed_file_reparses(self):
        calls, reader = self.counting_reader()
        ms_project.load(self.cfg, reader=reader)
        with open(self.source, "a", encoding="utf-8") as fh:
            fh.write("\n")
        again = ms_project.load(self.cfg, reader=reader)
        self.assertEqual(again["reader"], "mspdi")
        self.assertEqual(len(calls), 2)

    def test_missing_source_uses_cache(self):
        calls, reader = self.counting_reader()
        ms_project.load(self.cfg, reader=reader)
        self.source.unlink()
        data = ms_project.load(self.cfg)
        self.assertTrue(data["ok"])
        self.assertTrue(data["stale"])
        self.assertIn("cannot read", data["error"])
        self.assertEqual(len(data["projects"]), 9)
        self.assertEqual(len(data["assignments"]), 5)

    def test_missing_source_without_cache(self):
        self.source.unlink()
        data = ms_project.load(self.cfg)
        self.assertFalse(data["ok"])
        self.assertIn("cannot read", data["error"])

    def test_parse_error_uses_cache(self):
        calls, reader = self.counting_reader()
        ms_project.load(self.cfg, reader=reader)

        def broken(path, cfg):
            raise RuntimeError("boom")

        with open(self.source, "a", encoding="utf-8") as fh:
            fh.write("\n")
        data = ms_project.load(self.cfg, reader=broken)
        self.assertTrue(data["ok"])
        self.assertTrue(data["stale"])
        self.assertEqual(data["error"], "boom")

    def test_not_configured(self):
        data = ms_project.load({"ms_project": {}})
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "not configured")
        self.assertEqual(data["projects"], [])

    def test_settings_overlay_key(self):
        cfg = {
            "ms_project_path": str(self.source),
            "ms_project": {"cache_path": str(self.folder / "c2.json"), "copy_dir": str(self.folder / "copies")},
        }
        calls, reader = self.counting_reader()
        data = ms_project.load(cfg, reader=reader)
        self.assertTrue(data["ok"], data.get("error"))
        self.assertEqual(len(calls), 1)


class IsMeTests(unittest.TestCase):
    def test_by_email(self):
        self.assertTrue(ms_project.is_me({"name": "A Z", "email": "Anthony.Zunino@hamiltoncompany.com"}, {"me": ME}))

    def test_by_name(self):
        self.assertTrue(ms_project.is_me({"name": "  anthony   zunino ", "email": ""}, {"me": ME}))

    def test_name_only_resource(self):
        with tempfile.TemporaryDirectory() as folder:
            raw = ms_project.read_mspdi(write_fixture(folder))
        acme = by_customer(ms_project.normalise(raw), "Acme")
        self.assertTrue(any(ms_project.is_me(person, {"me": ME}) for person in acme["people"]))

    def test_placeholder_not_me(self):
        cfg = {"me": dict(ME), "ms_project": {}}
        self.assertFalse(ms_project.is_me({"name": "Needs Auto Eng", "email": ""}, cfg))
        self.assertFalse(ms_project.is_me({"name": "B4 Lab Space 9 (APE Star)", "email": ""}, cfg))
        self.assertFalse(ms_project.is_me({"name": "Needs PM", "email": ""}, cfg))

    def test_by_resource_name(self):
        cfg = {"me": {}, "ms_project": {"resource": "Needs Auto Eng"}}
        self.assertTrue(ms_project.is_me({"name": "needs auto eng", "email": ""}, cfg))

    def test_by_resource_email(self):
        cfg = {"me": {}, "ms_project": {"resource": "someone.else@hamiltoncompany.com"}}
        self.assertTrue(ms_project.is_me({"name": "", "email": "Someone.Else@hamiltoncompany.com"}, cfg))

    def test_not_me(self):
        self.assertFalse(ms_project.is_me({"name": "Someone Else", "email": "someone.else@hamiltoncompany.com"}, {"me": ME}))
        self.assertFalse(ms_project.is_me({}, {"me": ME}))


class MatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        raw = ms_project.read_mspdi(write_fixture(self.tmp.name))
        self.data = {"projects": ms_project.normalise(raw, PREFIXES)}
        self.cfg = {"me": dict(ME), "ms_project": {}}

    def test_by_so(self):
        project = Project(folder="x", name="SO-111111", so=["SO-111111"])
        found = ms_project.match(self.data, [project], self.cfg)
        self.assertEqual(found[project.key]["customer"], "Acme")

    def test_by_qte(self):
        project = Project(folder="x", name="QTE-047606", qte=["QTE-047606"])
        found = ms_project.match(self.data, [project], self.cfg)
        self.assertEqual(found[project.key]["customer"], "Delta")

    def test_no_match(self):
        project = Project(folder="x", name="SO-999999", so=["SO-999999"])
        self.assertEqual(ms_project.match(self.data, [project], self.cfg), {})

    def test_prefers_assigned(self):
        mine = [{"name": "Anthony Zunino", "email": "anthony.zunino@hamiltoncompany.com"}]
        data = {
            "projects": [
                schedule_project("1", "trt", ["SO-777777"], mine, "2027-05-01"),
                schedule_project("2", "assigned", ["SO-777777"], [], "2027-01-01"),
            ]
        }
        project = Project(folder="x", name="SO-777777", so=["SO-777777"])
        self.assertEqual(ms_project.match(data, [project], self.cfg)[project.key]["uid"], "2")

    def test_prefers_mine(self):
        mine = [{"name": "Anthony Zunino", "email": "anthony.zunino@hamiltoncompany.com"}]
        other = [{"name": "Someone Else", "email": "someone.else@hamiltoncompany.com"}]
        data = {
            "projects": [
                schedule_project("1", "assigned", ["SO-888888"], other, "2027-05-01"),
                schedule_project("2", "assigned", ["SO-888888"], mine, "2027-01-01"),
            ]
        }
        project = Project(folder="x", name="SO-888888", so=["SO-888888"])
        self.assertEqual(ms_project.match(data, [project], self.cfg)[project.key]["uid"], "2")

    def test_prefers_latest_finish(self):
        mine = [{"name": "Anthony Zunino", "email": "anthony.zunino@hamiltoncompany.com"}]
        data = {
            "projects": [
                schedule_project("1", "assigned", ["SO-666666"], mine, "2027-01-01"),
                schedule_project("2", "assigned", ["SO-666666"], mine, "2027-05-01"),
            ]
        }
        project = Project(folder="x", name="SO-666666", so=["SO-666666"])
        self.assertEqual(ms_project.match(data, [project], self.cfg)[project.key]["uid"], "2")

    def test_links_off_skips_project(self):
        project = Project(folder="x", name="SO-111111", so=["SO-111111"])
        self.assertEqual(ms_project.match(self.data, [project], self.cfg, links={project.key: "off"}), {})
        self.assertIn(project.key, ms_project.match(self.data, [project], self.cfg, links={}))

    def test_prefers_customer_match_whatever_the_order(self):
        mine = [{"name": "Anthony Zunino", "email": "anthony.zunino@hamiltoncompany.com"}]
        genentech = schedule_project("1", "assigned", ["SO-297328"], mine, "2027-01-01")
        genentech.update({"customer": "Genentech", "name": "Genentech - Triple ODTC Legacy Vant Upgrade -CH"})
        abbvie = schedule_project("2", "assigned", ["SO-297250", "SO-298096"], mine, "2027-01-01")
        abbvie.update({"customer": "Abbvie", "name": "Abbvie - Hettich Intg Upgrade - CH"})
        project = Project(
            folder="x", name="SO-297328", customer="Genentech", so=["SO-297328", "SO-298096"]
        )
        for items in ([genentech, abbvie], [abbvie, genentech]):
            found = ms_project.match({"projects": items}, [project], self.cfg)
            self.assertEqual(found[project.key]["uid"], "1")

    def test_prefers_more_overlap(self):
        mine = [{"name": "Anthony Zunino", "email": "anthony.zunino@hamiltoncompany.com"}]
        data = {
            "projects": [
                schedule_project("1", "assigned", ["SO-555001"], mine, "2027-05-01"),
                schedule_project("2", "assigned", ["SO-555001", "SO-555002"], mine, "2027-01-01"),
            ]
        }
        project = Project(folder="x", name="SO-555001", so=["SO-555001", "SO-555002"])
        self.assertEqual(ms_project.match(data, [project], self.cfg)[project.key]["uid"], "2")


class SchedulePendingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        raw = ms_project.read_mspdi(write_fixture(self.folder))
        self.data = {"projects": ms_project.normalise(raw, PREFIXES)}
        self.cfg = {"me": dict(ME), "ms_project": {}}

    def pending(self, projects=None, pending=None):
        return ms_project.schedule_pending(self.data, self.cfg, projects or [], pending or {}, NOW)

    def test_delta_pending(self):
        result = self.pending()
        self.assertEqual(
            list(result), ["schedule:14", "schedule:26", "schedule:31", "schedule:38", "schedule:41"]
        )
        entry = result["schedule:26"]
        self.assertEqual(entry["subject"], "Schedule - Delta | QTE-047606 | Table")
        self.assertEqual(entry["received"], "2026-11-09")
        self.assertEqual(entry["status"], "new")
        self.assertEqual(entry["source"], "schedule")
        self.assertEqual(entry["parsed"]["customer"], "Delta")
        self.assertEqual(entry["parsed"]["so"], [])
        self.assertEqual(entry["parsed"]["qte"], ["QTE-047606"])
        self.assertEqual(entry["parsed"]["description"], "Table")
        self.assertEqual(entry["suggested_folder"], "Delta QTE-047606")

    def test_same_keys_as_sheet_pending(self):
        sheet = ape_sheet.sheet_pending([SHEET_ROW], "AZ", [], {}, NOW)
        self.assertEqual(list(sheet), ["sheet-SO-999999"])
        self.assertEqual(set(self.pending()["schedule:26"]), set(sheet["sheet-SO-999999"]))

    def test_finished_and_other_excluded(self):
        result = self.pending()
        self.assertNotIn("schedule:28", result)
        self.assertNotIn("schedule:23", result)
        self.assertNotIn("schedule:11", result)

    def test_matched_excluded(self):
        projects = [
            Project(folder="x", name="Delta", qte=["QTE-047606"]),
            Project(folder="y", name="Acme", so=["SO-111111"]),
        ]
        self.assertEqual(list(self.pending(projects)), ["schedule:31", "schedule:38", "schedule:41"])

    def test_already_pending_excluded(self):
        pending = {"schedule:26": {"message_id": "schedule:26", "parsed": {"qte": ["QTE-047606"]}}}
        self.assertNotIn("schedule:26", self.pending(None, pending))

    def test_created_falls_back_to_now(self):
        delta = by_customer(self.data["projects"], "Delta")
        data = {"projects": [dict(delta, created="")]}
        entry = ms_project.schedule_pending(data, self.cfg, [], {}, NOW)["schedule:26"]
        self.assertEqual(entry["received"], NOW)

    def test_create_from_pending(self):
        vault = self.folder / "vault"
        vault.mkdir()
        cfg = dict(self.cfg)
        cfg["vault"] = str(vault)
        cfg["templates_dir"] = str(ROOT / "templates")
        entry = self.pending()["schedule:26"]
        info = projects_mod.create_from_pending(cfg, entry)
        self.assertTrue(Path(info["folder"]).is_dir())
        self.assertTrue(Path(info["note_path"]).is_file())
        self.assertEqual(entry["status"], "created")


class StripPrefixTests(unittest.TestCase):
    def test_real_style_names(self):
        cases = {
            "Acme - SO-111111 PreFabApps": "ms_prefab",
            "Acme - SO-111111 AE  Pack Up": "ms_pack_up",
            "Acme - SO-111111 AE Disassembly & Packing": "ms_pack_up",
            "Acme - SO-111111 AutoE Close Out": "ms_closeout",
            "Acme - SO-111111 Send Test Plan for Review & List of Parts for Testing": "ms_test_plan",
            "Acme - SO-111111 Testing": "ms_testing",
        }
        for name, expected in cases.items():
            stripped = extract.strip_project_prefix(name)
            milestone = rules_mod.milestone_for(RULES, stripped)
            self.assertIsNotNone(milestone, name)
            self.assertEqual(milestone["id"], expected, name)

    def test_double_space_collapsed(self):
        self.assertEqual(extract.strip_project_prefix("Acme - SO-111111 AE  Pack Up"), "AE Pack Up")


class BuildTasksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        raw = ms_project.read_mspdi(write_fixture(self.tmp.name))
        self.acme = by_customer(ms_project.normalise(raw, PREFIXES), "Acme")

    def test_schedule_rows_date_steps(self):
        rows = ms_project.schedule_rows(self.acme)
        self.assertEqual(
            rows[0], {"name": "PreFabApps", "start": "2026-11-04", "finish": "2026-12-04", "pct": 100}
        )
        rules = rules_mod.apply_checklist(RULES, {"checklist_optional": ["ms_prefab", "pack_up"]})
        project = Project(folder="x", name="SO-111111", project_no="0207001", so=["SO-111111"])
        tasks, _stage = build_tasks(
            project, {"assignment": date(2026, 11, 1)}, rules, [], TODAY, schedule=rows, level="L2"
        )
        found = {task.id.split("-", 1)[1]: task for task in tasks}
        self.assertEqual(found["system_setup"].due, date(2027, 1, 29))
        self.assertEqual(found["testing_demo"].due, date(2027, 1, 29))
        self.assertEqual(found["pack_up"].due, date(2027, 2, 3))
        self.assertEqual(found["co_package_files"].due, date(2027, 2, 12))
        self.assertEqual(found["time_estimate"].due, date(2026, 11, 3))

    def test_schedule_rows_stripped_names(self):
        rows = ms_project.schedule_rows(self.acme)
        self.assertEqual(
            [row["name"] for row in rows],
            [
                "PreFabApps",
                "Send Test Plan for Review & List of Parts for Testing",
                "Assembly",
                "Check Lead Times for Vendor Quotes",
                "Testing",
                "AE Pack Up",
                "AutoE Close Out",
            ],
        )
        self.assertEqual(
            sorted(milestone_rows(RULES, rows)),
            ["ms_closeout", "ms_pack_up", "ms_prefab", "ms_test_plan", "ms_testing"],
        )

    def test_schedule_rows_without_dash(self):
        with tempfile.TemporaryDirectory() as folder:
            raw = ms_project.read_mspdi(write_fixture(folder))
        moderna = by_customer(ms_project.normalise(raw), "Moderna")
        rows = ms_project.schedule_rows(moderna)
        self.assertEqual([row["name"] for row in rows], ["PreFabApps", "Testing"])
        self.assertEqual(sorted(milestone_rows(RULES, rows)), ["ms_prefab", "ms_testing"])

    def test_schedule_rows_empty(self):
        self.assertEqual(ms_project.schedule_rows(None), [])

    def test_schedule_rows_carry_pct(self):
        self.assertEqual([row["pct"] for row in ms_project.schedule_rows(self.acme)], [100, 0, 0, 0, 0, 0, 0])


class DueSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        raw = ms_project.read_mspdi(write_fixture(self.tmp.name))
        self.rows = ms_project.schedule_rows(by_customer(ms_project.normalise(raw, PREFIXES), "Acme"))
        self.rules = rules_mod.apply_checklist(
            RULES, {"checklist_optional": ["ms_prefab", "ms_testing", "adp_start", "pack_up"]}
        )
        self.project = Project(folder="x", name="SO-111111", project_no="0207001", so=["SO-111111"])

    def build(self, schedule, chart_source, triggers=None):
        tasks, _stage = build_tasks(
            self.project,
            {"assignment": date(2026, 11, 1)} if triggers is None else triggers,
            self.rules,
            [],
            TODAY,
            schedule=schedule,
            level="L2",
            chart_source=chart_source,
        )
        return {task.id.split("-", 1)[1]: task for task in tasks}

    def test_schedule_sourced_dates(self):
        found = self.build(self.rows, "schedule")
        self.assertEqual(found["ms_prefab"].due_source, "schedule")
        self.assertEqual(found["adp_start"].due_source, "schedule")
        self.assertEqual(found["ms_testing"].due_source, "schedule")
        self.assertEqual(found["pack_up"].due_source, "schedule")

    def test_email_chart_source(self):
        found = self.build(self.rows, "email")
        self.assertEqual(found["ms_prefab"].due_source, "email")
        self.assertEqual(found["adp_start"].due_source, "email")

    def test_trigger_offset_source(self):
        found = self.build(self.rows, "schedule")
        self.assertEqual(found["time_estimate"].due_source, "assignment+2")

    def test_undated_source(self):
        found = self.build(None, "")
        self.assertEqual(found["ms_prefab"].due_source, "")
        self.assertEqual(found["adp_start"].due_source, "")
        self.assertEqual(found["time_estimate"].due_source, "assignment+2")
        self.assertEqual(found["co_package_files"].due_source, "")


class ApplyScheduleDoneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        raw = ms_project.read_mspdi(write_fixture(self.tmp.name))
        self.rows = ms_project.schedule_rows(by_customer(ms_project.normalise(raw, PREFIXES), "Acme"))
        self.project = Project(folder="x", name="SO-111111", project_no="0207001", so=["SO-111111"])

    def build(self, rules, rows=None):
        tasks, _stage = build_tasks(
            self.project,
            {"assignment": date(2026, 11, 1)},
            rules,
            [],
            TODAY,
            schedule=self.rows if rows is None else rows,
            level="L2",
            chart_source="schedule",
        )
        return tasks

    def tick(self, rules, rows=None, ticked=None):
        rows = self.rows if rows is None else rows
        tasks = self.build(rules, rows)
        ticked = apply_schedule_done(
            tasks, milestone_rows(rules, rows), rules, {} if ticked is None else ticked, TODAY
        )
        return {task.id.split("-", 1)[1]: task for task in tasks}, ticked

    def test_complete_milestone_ticks_milestone_steps_and_loose_steps(self):
        rules = rules_mod.apply_checklist(
            RULES, {"checklist_optional": ["ms_prefab", "ms_test_plan", "adp_start", "review_cpr_so", "ace_questions"]}
        )
        found, ticked = self.tick(rules)
        self.assertTrue(found["ms_prefab"].done)
        self.assertEqual(found["ms_prefab"].mark, "x")
        self.assertEqual(found["ms_prefab"].done_date, date(2026, 12, 4))
        self.assertTrue(found["adp_start"].done)
        self.assertEqual(found["adp_start"].mark, "x")
        self.assertTrue(found["review_cpr_so"].done)
        self.assertTrue(found["ace_questions"].done)
        self.assertTrue(found["time_estimate"].done)
        self.assertEqual(found["review_cpr_so"].done_date, date(2026, 12, 4))
        self.assertFalse(found["ms_test_plan"].done)
        self.assertFalse(found["apps_plan"].done)
        self.assertIn(found["adp_start"].id, ticked)
        self.assertEqual(ticked[found["adp_start"].id], "2026-12-04")

    def test_incomplete_milestone_ticks_nothing(self):
        rules = rules_mod.apply_checklist(RULES, {"checklist_optional": ["ms_prefab", "adp_start"]})
        rows = [dict(row, pct=50) for row in self.rows]
        found, ticked = self.tick(rules, rows)
        self.assertFalse(any(task.done for task in found.values()))
        self.assertEqual(ticked, {})

    def test_na_task_untouched(self):
        rules = rules_mod.apply_checklist(RULES, {"checklist_optional": ["ms_prefab", "adp_start"]})
        tasks = self.build(rules)
        target = next(task for task in tasks if task.id.endswith("-adp_start"))
        target.done, target.mark = True, "-"
        ticked = apply_schedule_done(tasks, milestone_rows(rules, self.rows), rules, {}, TODAY)
        self.assertEqual(target.mark, "-")
        self.assertNotIn(target.id, ticked)

    def test_already_ticked_not_reticked(self):
        rules = rules_mod.apply_checklist(RULES, {"checklist_optional": ["ms_prefab", "adp_start"]})
        tasks = self.build(rules)
        target = next(task for task in tasks if task.id.endswith("-adp_start"))
        ticked = {target.id: "2026-01-02"}
        apply_schedule_done(tasks, milestone_rows(rules, self.rows), rules, ticked, TODAY)
        self.assertFalse(target.done)
        self.assertEqual(ticked[target.id], "2026-01-02")

    def test_disabled_milestone_steps_ticked_via_rules(self):
        rules = rules_mod.apply_checklist(RULES, {"checklist_optional": ["adp_start"]})
        found, ticked = self.tick(rules)
        self.assertNotIn("ms_prefab", found)
        self.assertEqual(found["adp_start"].parent, "")
        self.assertTrue(found["adp_start"].done)
        self.assertIn(found["adp_start"].id, ticked)

    def test_note_renders_ticked_step(self):
        rules = rules_mod.apply_checklist(RULES, {"checklist_optional": ["ms_prefab", "adp_start"]})
        tasks = self.build(rules)
        apply_schedule_done(tasks, milestone_rows(rules, self.rows), rules, {}, TODAY)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "n.md"
            path.write_text("**TO DO:**\n", encoding="utf-8")
            notes.apply_update(path, {}, tasks, TODAY)
            text = path.read_text(encoding="utf-8")
        self.assertIn("\n- [x] PreFabApps", text)
        self.assertIn("\n    - [x] Start ADP/APC draft (post to SharePoint)", text)


class PickScheduleTests(unittest.TestCase):
    MPP = [{"name": "Acme - SO-111111 Testing", "start": "2027-01-04", "finish": "2027-01-29"}]
    EMAIL = [{"name": "Acme - SO-111111 Testing", "start": "2027-02-01", "finish": "2027-02-26"}]

    def test_authoritative_prefers_schedule(self):
        rows, source = pick_schedule(RULES, self.MPP, self.EMAIL, True)
        self.assertEqual(source, "schedule")
        self.assertEqual(rows, self.MPP)

    def test_authoritative_falls_back_to_email(self):
        rows, source = pick_schedule(RULES, [{"name": "Notes", "start": "", "finish": ""}], self.EMAIL, True)
        self.assertEqual(source, "email")
        self.assertEqual(rows, self.EMAIL)

    def test_email_preferred_when_not_authoritative(self):
        rows, source = pick_schedule(RULES, self.MPP, self.EMAIL, False)
        self.assertEqual(source, "email")
        self.assertEqual(rows, self.EMAIL)

    def test_schedule_when_email_has_no_milestones(self):
        rows, source = pick_schedule(RULES, self.MPP, [], False)
        self.assertEqual(source, "schedule")
        self.assertEqual(rows, self.MPP)

    def test_nothing_available(self):
        self.assertEqual(pick_schedule(RULES, [], [], True), ([], ""))


class JavaHomeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)

    def make_java(self, *parts: str, version: str = "17.0.2") -> Path:
        home = self.folder.joinpath(*parts)
        (home / "bin" / "server").mkdir(parents=True, exist_ok=True)
        (home / "bin" / "java.exe").write_text("", encoding="utf-8")
        (home / "bin" / "server" / "jvm.dll").write_text("", encoding="utf-8")
        (home / "release").write_text('JAVA_VERSION="%s"\n' % version, encoding="utf-8")
        return home

    def test_old_java_on_path_is_skipped(self):
        old = self.make_java("jre8", version="1.8.0_361")
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": "", "JAVA_HOME": str(old)}):
            with mock.patch.object(ms_project.shutil, "which", return_value=str(old / "bin" / "java.exe")):
                self.assertEqual(ms_project.find_java_home({}), "")

    def test_stub_without_jvm_is_skipped(self):
        stub = self.folder / "javapath"
        (stub / "bin").mkdir(parents=True)
        (stub / "bin" / "java.exe").write_text("", encoding="utf-8")
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": "", "JAVA_HOME": str(stub)}):
            with mock.patch.object(ms_project.shutil, "which", return_value=None):
                self.assertEqual(ms_project.find_java_home({}), "")

    def test_localappdata_layout(self):
        home = self.make_java("ProjectHub", "jre", "jdk-17")
        env = {"LOCALAPPDATA": str(self.folder), "JAVA_HOME": ""}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(ms_project.find_java_home({}), str(home))

    def test_cfg_java_home_wins(self):
        home = self.make_java("jdk")
        cfg = {"ms_project": {"java_home": str(home)}}
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": "", "JAVA_HOME": ""}):
            self.assertEqual(ms_project.find_java_home(cfg), str(home))

    def test_java_home_env(self):
        home = self.make_java("envjdk")
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": "", "JAVA_HOME": str(home)}):
            self.assertEqual(ms_project.find_java_home({}), str(home))

    def test_none_found(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(self.folder), "JAVA_HOME": ""}):
            with mock.patch.object(ms_project.shutil, "which", return_value=None):
                self.assertEqual(ms_project.find_java_home({}), "")


class MpxjTests(unittest.TestCase):
    def test_missing_package_message(self):
        try:
            import mpxj  # noqa: F401
        except ImportError:
            pass
        else:
            self.skipTest("mpxj is installed")
        with self.assertRaises(RuntimeError) as caught:
            ms_project.read_mpxj("missing.mpp")
        self.assertIn("MPXJ is not installed", str(caught.exception))


def integration_ready() -> bool:
    target = os.environ.get("PROJECTHUB_MPP") or ""
    if not target or not Path(target).is_file():
        return False
    if not ms_project.find_java_home({}):
        return False
    try:
        import mpxj  # noqa: F401
    except ImportError:
        return False
    return True


@unittest.skipUnless(integration_ready(), "set PROJECTHUB_MPP and install Java 11+ with mpxj")
class IntegrationTests(unittest.TestCase):
    def test_loads_real_schedule(self):
        config_path = ROOT / "tracker" / "config.yaml"
        me = dict(ME)
        if config_path.is_file():
            me = dict((yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}).get("me") or me)
        with tempfile.TemporaryDirectory() as folder:
            cfg = {
                "me": me,
                "ms_project": {
                    "path": os.environ["PROJECTHUB_MPP"],
                    "cache_path": str(Path(folder) / "cache.json"),
                    "copy_dir": str(Path(folder) / "copies"),
                },
            }
            data = ms_project.load(cfg)
        self.assertTrue(data["ok"], data.get("error"))
        self.assertGreater(len(data["projects"]), 50)
        self.assertTrue(data["assignments"])
        mine = [
            item
            for item in data["projects"]
            if any(ms_project.is_me(person, cfg) for person in item.get("people") or ())
        ]
        if config_path.is_file():
            self.assertTrue(mine)
        self.assertGreaterEqual(
            max(len(milestone_rows(RULES, ms_project.schedule_rows(item))) for item in mine), 3
        )


class HubScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()
        self.folder = self.vault / "Acme SO-1 0207000"
        self.folder.mkdir()
        self.note = self.folder / "Acme SO-1 0207000.md"
        self.note.write_text(HUB_NOTE, encoding="utf-8")
        self.state_path = self.root / "state.json"
        self.snapshot_path = self.root / "snapshot.json"
        self.config_path = self.root / "config.yaml"
        self.cfg = {
            "vault": str(self.vault),
            "state_path": str(self.state_path),
            "snapshot_path": str(self.snapshot_path),
            "log_path": str(self.root / "last_run.log"),
            "hub": {"port": 8765, "refresh_minutes": 15, "toast": False},
        }
        self.config_path.write_text(yaml.safe_dump(self.cfg), encoding="utf-8")
        self.state_path.write_text(json.dumps({"ui": {}, "pending": {}}), encoding="utf-8")
        self.write_snapshot()
        self.app = create_app(self.config_path, port=8765, no_refresh=True, runner=lambda: 0)
        self.app.testing = True
        self.client = self.app.test_client()
        self.token = self.app.config["HUB_TOKEN"]

    def write_snapshot(
        self,
        ms_project=None,
        schedule_mine=None,
        schedule_source="schedule",
        tasks=None,
        schedule_project="",
        schedule_unlinked=False,
    ):
        project = {
            "key": "0207000",
            "name": "Acme SO-1 0207000",
            "folder": str(self.folder),
            "note_path": str(self.note),
            "customer": "Acme",
            "project_no": "0207000",
            "so": [],
            "qte": [],
            "stage": "Pre-Fabrication",
            "team": [],
            "links": [],
            "tasks": list(tasks or []),
            "emails": [],
            "schedule_source": schedule_source,
            "schedule_mine": schedule_mine,
            "schedule_project": schedule_project,
            "schedule_unlinked": schedule_unlinked,
        }
        snap = {
            "generated": "2026-11-10T06:30:00-08:00",
            "today": "2026-11-10",
            "projects": [project],
            "pending": [],
            "unmatched": [],
        }
        if ms_project is not None:
            snap["ms_project"] = ms_project
        self.snapshot_path.write_text(json.dumps(snap), encoding="utf-8")

    def page(self, url: str) -> str:
        return self.client.get(url, headers=HOST).get_data(as_text=True)

    def post(self, url: str, payload: dict):
        headers = dict(HOST)
        headers["X-Hub-Token"] = self.token
        headers["Origin"] = ORIGIN
        return self.client.post(url, json=payload, headers=headers)

    def overlay(self) -> dict:
        return json.loads((self.root / "settings.json").read_text(encoding="utf-8"))

    def test_dashboard_schedule_line(self):
        self.write_snapshot(
            {
                "ok": True,
                "error": "",
                "stale": False,
                "source": "R:/APE_AE/Project/APE Group Schedule.mpp",
                "file_mtime": "2026-11-20T08:15:00-08:00",
                "reader": "mpxj",
                "projects": 12,
                "mine": 3,
                "matched": 5,
            }
        )
        page = self.page("/")
        self.assertIn("Schedule: APE Group Schedule.mpp", page)
        label = datetime.fromisoformat("2026-11-20T08:15:00-08:00").astimezone().strftime("%a %d %b %H:%M")
        self.assertIn("file dated %s" % label, page)
        self.assertIn("3 mine / 5 matched", page)

    def test_dashboard_schedule_stale_line(self):
        self.write_snapshot(
            {
                "ok": True,
                "error": "cannot read R:/APE_AE/Project/APE Group Schedule.mpp",
                "stale": True,
                "source": "R:/APE_AE/Project/APE Group Schedule.mpp",
                "file_mtime": "2026-11-20T08:15:00-08:00",
                "reader": "cache",
                "projects": 12,
                "mine": 3,
                "matched": 5,
            }
        )
        page = self.page("/")
        self.assertIn("Schedule: using cached copy (cannot read", page)
        self.assertIn("sheet-status-line stale", page)

    def test_dashboard_without_schedule_block(self):
        page = self.page("/")
        self.assertNotIn("Schedule:", page)

    def test_dashboard_schedule_error_line(self):
        self.write_snapshot({"ok": False, "error": "java not found", "stale": False, "source": "", "mine": 0, "matched": 0})
        self.assertIn("Schedule: java not found", self.page("/"))

    def test_dashboard_schedule_not_configured_hidden(self):
        self.write_snapshot({"ok": False, "error": "not configured", "stale": False, "source": "", "mine": 0, "matched": 0})
        self.assertNotIn("Schedule:", self.page("/"))

    def test_project_page_dates_and_tag(self):
        self.write_snapshot(
            {"ok": True, "error": "", "stale": False, "source": "R:/APE_AE/Project/APE Group Schedule.mpp", "mine": 0, "matched": 1},
            schedule_mine=False,
            schedule_project="Acme - Widget Integration - CH",
        )
        page = self.page("/p/0207000")
        self.assertIn("Dates from APE Group Schedule", page)
        self.assertIn("Not assigned to you in schedule", page)

    def test_project_page_email_dates_without_tag(self):
        self.write_snapshot(None, schedule_mine=None, schedule_source="email")
        page = self.page("/p/0207000")
        self.assertIn("Dates from kickoff email chart", page)
        self.assertNotIn("Not assigned to you in schedule", page)

    def test_project_page_schedule_project_link(self):
        self.write_snapshot(None, schedule_mine=None, schedule_project="X - Y - CH")
        page = self.page("/p/0207000")
        self.assertIn("X - Y - CH", page)
        self.assertIn("Unlink", page)
        self.assertNotIn("Use schedule again", page)

    def test_project_page_schedule_unlinked(self):
        self.write_snapshot(None, schedule_mine=None, schedule_unlinked=True)
        page = self.page("/p/0207000")
        self.assertIn("Use schedule again", page)
        self.assertNotIn("Unlink", page)

    def test_schedule_link_endpoint(self):
        response = self.post("/api/projects/schedule_link", {"key": "0207000", "linked": False})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"key": "0207000", "linked": False})
        saved = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["schedule_links"]["0207000"], "off")
        response = self.post("/api/projects/schedule_link", {"key": "0207000", "linked": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"key": "0207000", "linked": True})
        saved = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["schedule_links"], {})
        self.assertEqual(
            self.post("/api/projects/schedule_link", {"key": "nope", "linked": False}).status_code, 404
        )

    def test_project_page_due_source_labels(self):
        tasks = [
            {
                "id": "p0207000-ms_prefab",
                "title": "PreFabApps",
                "stage": "Pre-Fabrication",
                "due": "2026-12-04",
                "status": "open",
                "mark": " ",
                "source": "tracker",
                "parent": "",
                "milestone": True,
                "mandatory": False,
                "due_source": "schedule",
            },
            {
                "id": "p0207000-time_estimate",
                "title": "Time Estimate (in Project Page)",
                "stage": "Pre-Fabrication",
                "due": "2026-11-03",
                "status": "open",
                "mark": " ",
                "source": "tracker",
                "parent": "",
                "milestone": False,
                "mandatory": True,
                "due_source": "assignment+2",
            },
        ]
        self.write_snapshot(None, schedule_mine=None, tasks=tasks)
        page = self.page("/p/0207000")
        self.assertIn("(schedule)", page)
        self.assertIn("(from Project assignment +2)", page)
        self.assertNotIn("(computed)", page)

    def test_board_card_tag(self):
        self.write_snapshot(None, schedule_mine=False)
        self.assertIn("Not assigned to you in schedule", self.page("/"))
        self.write_snapshot(None, schedule_mine=None)
        self.assertNotIn("Not assigned to you in schedule", self.page("/"))

    def test_pending_schedule_tag(self):
        entry = {
            "message_id": "schedule:13",
            "subject": "Schedule - Delta | QTE-047606 | Table",
            "received": "2026-11-10",
            "status": "new",
            "source": "schedule",
            "parsed": {"customer": "Delta", "qte": ["QTE-047606"], "project_no": "0207002", "level": "L2"},
        }
        self.state_path.write_text(json.dumps({"ui": {}, "pending": {"schedule:13": entry}}), encoding="utf-8")
        page = self.page("/")
        self.assertIn("From schedule", page)
        self.assertIn("Schedule - Delta | QTE-047606 | Table", page)

    def test_settings_ms_project_path(self):
        schedule = self.root / "APE Group Schedule.xml"
        schedule.write_text("<Project/>", encoding="utf-8")
        expected = str(schedule).replace("\\", "/")
        response = self.post("/api/settings", {"ms_project_path": str(schedule)})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["rerun"])
        self.assertEqual(response.get_json()["settings"]["ms_project_path"], expected)
        self.assertEqual(self.overlay()["ms_project_path"], expected)
        self.assertIn('id="set-ms-project"', self.page("/settings"))
        self.assertEqual(
            self.post("/api/settings", {"ms_project_path": str(self.root / "nope.mpp")}).status_code, 400
        )
        self.assertEqual(self.post("/api/settings", {"ms_project_path": ""}).status_code, 200)
        self.assertNotIn("ms_project_path", self.overlay())


class StateTests(unittest.TestCase):
    def test_load_defaults_schedule_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            self.assertEqual(state_mod.load(path)["schedule_links"], {})
            path.write_text(json.dumps({"ui": {}}), encoding="utf-8")
            self.assertEqual(state_mod.load(path)["schedule_links"], {})


if __name__ == "__main__":
    unittest.main()
