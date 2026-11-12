from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tracker import extract

FIXTURES = ROOT / "tests" / "fixtures" / "real_emails.json"


def load_fixtures() -> list[dict]:
    with open(FIXTURES, "r", encoding="utf-8") as fh:
        return json.load(fh)


def assignments() -> list[dict]:
    return [item for item in load_fixtures() if "Project Assignment" in item["subject"]]


def kickoffs() -> list[dict]:
    return [item for item in load_fixtures() if "Kickoff" in item["subject"]]


def fixture(subject_part: str) -> dict:
    for item in load_fixtures():
        if subject_part in item["subject"]:
            return item
    raise AssertionError("fixture not found: %s" % subject_part)


class AssignmentBodyTests(unittest.TestCase):
    def test_sharepoint_and_team(self):
        items = assignments()
        self.assertTrue(items)
        for item in items:
            parsed = extract.parse_assignment_body(item["body"])
            self.assertTrue(parsed["sharepoint"].startswith("https://"), item["subject"])
            self.assertIn("Anthony Zunino", parsed["team"], item["subject"])

    def test_checklist_items(self):
        parsed = extract.parse_assignment_body(fixture("Exact |").get("body", ""))
        self.assertEqual(len(parsed["checklist"]), 11)
        self.assertTrue(parsed["checklist"][0].startswith("Review the pipettor configuration"))
        self.assertFalse(any("Evaluate potential" in item for item in parsed["checklist"]))

    def test_greeting_and_rmt(self):
        parsed = extract.parse_assignment_body(fixture("Neogenomics |")["body"])
        self.assertEqual(parsed["pm_first_name"], "Cindy")
        self.assertTrue(parsed["rmt_note"])
        self.assertEqual(parsed["team"], ["Daniel Adams", "Anthony Zunino"])

    def test_missing_body(self):
        parsed = extract.parse_assignment_body("")
        self.assertEqual(parsed["sharepoint"], "")
        self.assertEqual(parsed["team"], [])
        self.assertEqual(parsed["checklist"], [])


class KickoffBodyTests(unittest.TestCase):
    def test_links_present(self):
        items = kickoffs()
        self.assertEqual(len(items), 4)
        for item in items:
            parsed = extract.parse_kickoff_body(item["body"])
            self.assertTrue(parsed["project_page"].startswith("https://"), item["subject"])
            self.assertTrue(parsed["cpr"].startswith("https://"), item["subject"])
            self.assertTrue(parsed["rmt"].startswith("https://"), item["subject"])

    def test_ship_dates(self):
        expected = {
            "Hamilton Australia SO-": "2027-02-23",
            "Amgen QTE-": "2027-01-28",
            "Neogenomics SO-": "2026-12-22",
            "Medimmune - SO-": "2027-01-27",
        }
        for part, value in expected.items():
            parsed = extract.parse_kickoff_body(fixture(part)["body"])
            self.assertEqual(parsed["ship_date"], value, part)

    def test_level_and_design_need_by(self):
        parsed = extract.parse_kickoff_body(fixture("Neogenomics SO-")["body"])
        self.assertEqual(parsed["level"], "L1")
        self.assertIsNone(parsed["design_need_by"])
        self.assertEqual(parsed["design_need_by_text"], "DONE and APPROVED")
        parsed = extract.parse_kickoff_body(fixture("Amgen QTE-")["body"])
        self.assertEqual(parsed["level"], "L2")
        self.assertEqual(parsed["design_need_by"], "2026-11-12")
        self.assertIn("partial shipment", parsed["ship_date_text"])

    def test_custom_parts(self):
        parsed = extract.parse_kickoff_body(fixture("Medimmune - SO-")["body"])
        self.assertEqual(len(parsed["custom_parts"]), 2)
        self.assertIn("0207358-003", parsed["custom_parts"][0])


class LinkTests(unittest.TestCase):
    def test_titles_and_dedupe(self):
        body = (
            "Project page  here <https://example.com/a> \r\n"
            "CPR form  here <https://example.com/b> \r\n"
            "again <https://example.com/a> \r\n"
            "mail <mailto:someone@example.com> \r\n"
        )
        links = extract.extract_links(body)
        self.assertEqual([link["url"] for link in links], ["https://example.com/a", "https://example.com/b"])
        self.assertEqual(links[0]["title"], "Project page")
        self.assertEqual(links[1]["title"], "CPR form")

    def test_zero_width_stripped(self):
        links = extract.extract_links("CPR \u200bhere <https://example.com/c>")
        self.assertEqual(links[0]["title"], "CPR")


class ScheduleTests(unittest.TestCase):
    def test_medimmune_schedule(self):
        rows = extract.parse_schedule(fixture("Medimmune - SO-")["body"], "Anthony Zunino")
        self.assertTrue(rows)
        by_name = {row["name"]: row for row in rows}
        test_plan = [row for row in rows if "Send Test Plan for Review" in row["name"]]
        self.assertEqual(len(test_plan), 1)
        self.assertEqual(test_plan[0]["finish"], "2026-11-12")
        self.assertEqual(by_name["Instrument Need-by Date"]["finish"], "2026-12-16")
        self.assertEqual(by_name["AstraZeneca - SO-295698, SO-295546, SO-296000 PreFabApps"]["resources"], "Cristian Uribe")

    def test_hamilton_australia_schedule(self):
        rows = extract.parse_schedule(fixture("Hamilton Australia SO-")["body"], "Anthony Zunino")
        self.assertTrue(rows)
        mine = [row for row in rows if row["mine"]]
        self.assertTrue(mine)
        self.assertTrue(all(row["resources"] == "Anthony Zunino" for row in mine))
        self.assertEqual(rows[0]["name"], "Hamilton Australia - SO-294084 Kick-Off")
        mine_names = {row["name"]: row["finish"] for row in mine}
        self.assertEqual(mine_names["Hamilton Australia - SO-294084 PreFabApps"], "2026-11-03")
        self.assertEqual(
            mine_names["Hamilton Australia - SO-294084 Send Test Plan for Review & List of Parts for Testing"],
            "2026-12-01",
        )

    def test_amgen_table_without_header(self):
        rows = extract.parse_schedule(fixture("Amgen QTE-056793 Kickoff")["body"], "Anthony Zunino")
        self.assertEqual(rows[0]["name"], "Amgen - QTE-056793 Kick-Off")
        self.assertFalse(any(row["name"].startswith("Amgen - STAR") for row in rows))
        by_name = {row["name"]: row for row in rows}
        self.assertEqual(by_name["Amgen - QTE-056793 Ship Date"]["finish"], "2027-01-28")
        self.assertTrue(all(row["resources"] == "" for row in rows))

    def test_other_fixtures_empty(self):
        for item in load_fixtures():
            if any(part in item["subject"] for part in ("Medimmune - SO-", "Hamilton Australia SO-", "Amgen QTE-056793 Kickoff")):
                continue
            self.assertEqual(extract.parse_schedule(item["body"], "Anthony Zunino"), [], item["subject"])

    def test_project_prefix_stripped(self):
        self.assertEqual(
            extract.strip_project_prefix(
                "AstraZeneca - SO-295698, SO-295546, SO-296000 Send Test Plan for Review"
            ),
            "Send Test Plan for Review",
        )
        self.assertEqual(extract.strip_project_prefix("Instrument Need-by Date"), "Instrument Need-by Date")
        self.assertEqual(
            extract.strip_project_prefix("Amgen - QTE-056793 - Assembly"),
            "Amgen - QTE-056793 - Assembly",
        )


if __name__ == "__main__":
    unittest.main()
