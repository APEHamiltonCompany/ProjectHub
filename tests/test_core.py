from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tracker import extract, matcher, notes
from tracker import projects as projects_mod
from tracker import rules as rules_mod
from tracker.migrate_notes import migrate_note
from tracker.models import Email, Project, Task
from tracker.sources import eml_folder
from tracker.tracker import (
    apply_due_overrides,
    build_snapshot,
    collect_trigger_dates,
    merge_block_state,
    unmatched_entry,
)

SAMPLE_EML = ROOT / (
    "Project Assignment - Medimmune _ SO-295546, SO-296000 _ 0207846 _ "
    "Mirage and SpectraMax B Cabinet Integration _ L2.eml"
)
HOLIDAYS = [date(2026, 11, 26), date(2026, 11, 27)]


class BusinessDayTests(unittest.TestCase):
    def test_friday_plus_two(self):
        self.assertEqual(rules_mod.add_business_days(date(2026, 11, 6), 2, HOLIDAYS), date(2026, 11, 10))

    def test_saturday_plus_two(self):
        self.assertEqual(rules_mod.add_business_days(date(2026, 11, 7), 2, HOLIDAYS), date(2026, 11, 10))

    def test_holiday_skip(self):
        self.assertEqual(rules_mod.add_business_days(date(2026, 11, 25), 2, HOLIDAYS), date(2026, 12, 1))

    def test_zero_offset(self):
        self.assertEqual(rules_mod.add_business_days(date(2026, 11, 6), 0, HOLIDAYS), date(2026, 11, 6))


class SubjectTests(unittest.TestCase):
    def test_assignment_subject(self):
        subject = (
            "Project Assignment - Medimmune | SO-295546, SO-296000 | 0207846 | "
            "Mirage and SpectraMax B Cabinet Integration | L2"
        )
        parsed = matcher.parse_assignment_subject(subject)
        self.assertEqual(parsed["customer"], "Medimmune")
        self.assertEqual(parsed["so"], ["SO-295546", "SO-296000"])
        self.assertEqual(parsed["project_no"], "0207846")
        self.assertEqual(parsed["level"], "L2")
        self.assertEqual(parsed["description"], "Mirage and SpectraMax B Cabinet Integration")

    def test_doubled_prefix(self):
        subject = "Project Assignment - Project Assignment - Amgen Inc. | QTE-056793 | 0207852 | STAR LHS HiG INTG | L2"
        parsed = matcher.parse_assignment_subject(subject)
        self.assertEqual(parsed["customer"], "Amgen Inc.")
        self.assertEqual(parsed["qte"], ["QTE-056793"])
        self.assertEqual(parsed["project_no"], "0207852")

    def test_keys(self):
        keys = matcher.extract_keys("SO-295298 0207779-001 QTE-056793")
        self.assertEqual(keys["so"], {"SO-295298"})
        self.assertEqual(keys["qte"], {"QTE-056793"})
        self.assertEqual(keys["project_no"], {"0207779"})


class EmlTests(unittest.TestCase):
    def test_sample_eml(self):
        if not SAMPLE_EML.is_file():
            self.skipTest("sample eml not present (real emails are gitignored)")
        parsed = eml_folder.parse_eml(SAMPLE_EML)
        self.assertIsNotNone(parsed)
        self.assertEqual(
            parsed.subject,
            "Project Assignment - Medimmune | SO-295546, SO-296000 | 0207846 | "
            "Mirage and SpectraMax B Cabinet Integration | L2",
        )
        self.assertEqual(parsed.date.isoformat(), "2026-11-06T18:02:16+00:00")
        self.assertIn("estimates are due within two days", parsed.body_text)
        self.assertIn("APEadmin@hamiltoncompany.com", parsed.sender)


class ManagedBlockTests(unittest.TestCase):
    def _note(self, folder: Path, text: str, newline: str = "\n") -> Path:
        path = folder / "note.md"
        path.write_bytes(text.replace("\n", newline).encode("utf-8"))
        return path

    def test_idempotent_and_ticked_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path = self._note(
                folder,
                "**06Nov2026**\n\n**TO DO:** (Mark done items with #)\n\n\n\n**AE Order Review Checklist:**\n",
            )
            tasks = [
                Task(id="p0207846-time_estimate", title="Time estimate", due=date(2026, 11, 10)),
                Task(id="p0207846-adp_start", title="Start ADP/APC draft"),
            ]
            changed, _diff = notes.apply_update(path, {"project_no": "0207846"}, tasks, date(2026, 11, 6))
            self.assertTrue(changed)
            first = path.read_text(encoding="utf-8")
            self.assertIn(notes.BLOCK_START, first)
            self.assertIn("p0207846-time_estimate", first)
            self.assertIn("project_no: '0207846'", first)

            changed, _diff = notes.apply_update(path, {"project_no": "0207846"}, tasks, date(2026, 11, 6))
            self.assertFalse(changed, "second run must not change the note")

            text = path.read_text(encoding="utf-8").replace(
                "- [ ] Time estimate", "- [x] Time estimate"
            )
            path.write_text(text, encoding="utf-8")
            changed, _diff = notes.apply_update(path, {"project_no": "0207846"}, tasks, date(2026, 11, 7))
            self.assertFalse(changed, "a user-ticked line is left exactly as written")
            self.assertIn("- [x] Time estimate", path.read_text(encoding="utf-8"))
            self.assertNotIn("\u2705", path.read_text(encoding="utf-8"))

    def test_user_lines_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path = self._note(folder, "**TO DO:** (Mark done items with #)\n\n**AE Order Review Checklist:**\n")
            tasks = [Task(id="p1-time_estimate", title="Time estimate", due=date(2026, 11, 10))]
            notes.apply_update(path, {}, tasks, date(2026, 11, 6))
            text = path.read_text(encoding="utf-8")
            text = text.replace(
                notes.BLOCK_END, "- [ ] my own reminder\n" + notes.BLOCK_END
            )
            path.write_text(text, encoding="utf-8")
            notes.apply_update(path, {}, tasks, date(2026, 11, 6))
            self.assertIn("- [ ] my own reminder", path.read_text(encoding="utf-8"))

    def test_crlf_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path = self._note(
                folder,
                "**TO DO:** (Mark done items with #)\n\n**AE Order Review Checklist:**\n",
                newline="\r\n",
            )
            tasks = [Task(id="p1-time_estimate", title="Time estimate", due=date(2026, 11, 10))]
            notes.apply_update(path, {}, tasks, date(2026, 11, 6))
            raw = path.read_bytes()
            self.assertIn(b"\r\n", raw)
            self.assertEqual(raw.count(b"\n"), raw.count(b"\r\n"))


class ReadOnlyTests(unittest.TestCase):
    def _tasks(self):
        return [
            Task(id="p1-time_estimate", title="Time estimate", due=date(2026, 11, 10)),
            Task(id="p1-review_cpr_so", title="Review CPR/SO", due=date(2026, 11, 10)),
        ]

    def test_struck_through_text_never_ticks(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "note.md"
            path.write_text(
                "**TO DO:** (Mark done items with #)\n\t~~send out time estimates~~\n# review cpr\n\n"
                "**AE Order Review Checklist:**\n",
                encoding="utf-8",
            )
            notes.apply_update(path, {}, self._tasks(), date(2026, 11, 6))
            text = path.read_text(encoding="utf-8")
            self.assertIn("- [ ] Time estimate", text)
            self.assertIn("- [ ] Review CPR/SO", text)
            self.assertNotIn("[x]", text)

    def test_user_marks_read_and_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "note.md"
            path.write_text("**TO DO:**\n\n**AE Order Review Checklist:**\n", encoding="utf-8")
            notes.apply_update(path, {}, self._tasks(), date(2026, 11, 6))
            text = path.read_text(encoding="utf-8")
            text = text.replace("- [ ] Time estimate", "- [-] Time estimate").replace(
                "- [ ] Review CPR/SO", "- [x] Review CPR/SO"
            )
            path.write_text(text, encoding="utf-8")
            tasks = self._tasks()
            _d, _r, body = notes.split_frontmatter(text)
            merge_block_state(tasks, body)
            self.assertEqual(tasks[0].status, "not needed")
            self.assertEqual(tasks[1].status, "done")
            changed, _diff = notes.apply_update(path, {}, tasks, date(2026, 11, 7))
            self.assertFalse(changed)


class MigrationTests(unittest.TestCase):
    def test_dedupe_and_checkbox_conversion(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "Exact SO-296266"
            folder.mkdir()
            path = folder / "Exact  SO-296266.md"
            first = (
                "**06Nov2026**\n\n"
                "**Project Assignment -  Exact | SO-296266 | 0207840 | STAR23 | L2\n\n"
                "**TO DO:** (Mark done items with #)\n - Send time estimates\n\n"
                "**AE Order Review Checklist:**\n\n"
                "[] Review the pipettor configuration\n"
                "[x] Confirm all custom parts\n"
            )
            second = first.replace(" - Send time estimates", "    - Send time estimates")
            path.write_text(first + "\n" + second, encoding="utf-8")

            changes, new_text = migrate_note(path, dry_run=False)
            self.assertTrue(any("duplicated" in c for c in changes))
            self.assertTrue(any("checkbox" in c for c in changes))
            self.assertEqual(new_text.count("**06Nov2026**"), 1)
            self.assertIn("- [ ] Review the pipettor configuration", new_text)
            self.assertIn("- [x] Confirm all custom parts", new_text)
            self.assertIn("project_no: '0207840'", new_text)
            self.assertIn("assigned: '2026-11-06'", new_text)

            changes, again = migrate_note(path, dry_run=False)
            self.assertEqual(changes, [])
            self.assertEqual(again, new_text)

    def test_already_correct_checkboxes_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "Neogenomics SO-295298 0207779-001 Plate Downhold Module L1"
            folder.mkdir()
            path = folder / (folder.name + ".md")
            path.write_text(
                "**29Oct2026**\n\n**TO DO:** (Mark done items with #)\n\n"
                "**AE Order Review Checklist:**\n\n- [ ] Review the pipettor configuration\n",
                encoding="utf-8",
            )
            changes, new_text = migrate_note(path, dry_run=False)
            self.assertIn("- [ ] Review the pipettor configuration", new_text)
            self.assertNotIn("- [ ] - [ ]", new_text)


class BlockGroupingTests(unittest.TestCase):
    def _tasks(self) -> list[Task]:
        return [
            Task(id="p1-review_cpr_so", title="Review CPR/SO", stage="Pre-Fabrication", due=date(2026, 11, 10)),
            Task(id="p1-time_estimate", title="Time estimate", stage="Pre-Fabrication", due=date(2026, 11, 10)),
            Task(id="p1-app_dev", title="Preliminary apps", stage="System Test"),
            Task(id="p1-co_methods", title="Methods posted", stage="Closeout"),
            Task(id="p1-retrospective", title="Retrospective", stage="Completion"),
            Task(id="p1-pm-test", title="Send Test Plan", stage=notes.PM_HEADING, due=date(2026, 11, 12)),
        ]

    def _note(self, folder: Path) -> Path:
        path = folder / "note.md"
        path.write_text("**TO DO:** (Mark done items with #)\n\n**AE Order Review Checklist:**\n", encoding="utf-8")
        return path

    def test_headings_once_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._note(Path(tmp))
            changed, _diff = notes.apply_update(path, {}, self._tasks(), date(2026, 11, 6))
            self.assertTrue(changed)
            text = path.read_text(encoding="utf-8")
            for heading in ("Pre-Fabrication", "System Test", "Closeout", "Completion", "PM schedule"):
                self.assertEqual(text.count("**%s**" % heading), 1, heading)
            self.assertLess(text.index("**Pre-Fabrication**"), text.index("**System Test**"))
            self.assertLess(text.index("**Completion**"), text.index("**PM schedule**"))
            self.assertLess(text.index("p1-review_cpr_so"), text.index("p1-time_estimate"))
            changed, _diff = notes.apply_update(path, {}, self._tasks(), date(2026, 11, 6))
            self.assertFalse(changed)
            self.assertEqual(path.read_text(encoding="utf-8"), text)

    def test_user_mark_and_user_line_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._note(Path(tmp))
            notes.apply_update(path, {}, self._tasks(), date(2026, 11, 6))
            text = path.read_text(encoding="utf-8")
            text = text.replace("- [ ] Time estimate", "- [-] Time estimate")
            text = text.replace(notes.BLOCK_END, "- [ ] my own reminder\n" + notes.BLOCK_END)
            path.write_text(text, encoding="utf-8")
            notes.apply_update(path, {}, self._tasks(), date(2026, 11, 6))
            again = path.read_text(encoding="utf-8")
            self.assertIn("- [-] Time estimate", again)
            self.assertEqual(again.count("- [ ] my own reminder"), 1)
            for heading in ("Pre-Fabrication", "System Test", "Closeout", "Completion", "PM schedule"):
                self.assertEqual(again.count("**%s**" % heading), 1, heading)


class FrontmatterFillTests(unittest.TestCase):
    def test_existing_values_kept_missing_filled(self):
        data = {"project_no": "0207846", "sharepoint": "https://user", "cpr": "", "stage": "Design"}
        merged = notes.merge_frontmatter(
            data,
            {
                "sharepoint": "https://email",
                "cpr": "https://cpr",
                "rmt": "https://rmt",
                "team": ["Samin Chowdhury", "Anthony Zunino"],
                "stage": "Closeout",
            },
        )
        self.assertEqual(merged["sharepoint"], "https://user")
        self.assertEqual(merged["cpr"], "https://cpr")
        self.assertEqual(merged["rmt"], "https://rmt")
        self.assertEqual(merged["team"], ["Samin Chowdhury", "Anthony Zunino"])
        self.assertEqual(merged["stage"], "Closeout")
        self.assertEqual(list(merged)[:2], ["project_no", "stage"])


def sample_email(message_id: str = "m1") -> Email:
    return Email(
        message_id=message_id,
        subject="Project Assignment - Foo | SO-123456 | 0207999 | Bar | L2",
        sender="APE admin",
        to="",
        cc="",
        date=datetime(2026, 11, 6, 10, 0, tzinfo=timezone.utc),
        body_text=(
            "Scott, this project has been assigned to Samin Chowdhury and Anthony Zunino.\r\n\r\n"
            "Sharepoint Link <https://example.com/p> \r\n\r\n"
            "AE Order Review Checklist:\r\n\u2610 One\r\n\u2610 Two\r\n"
        ),
    )


class PendingTests(unittest.TestCase):
    def _cfg(self, tmp: str) -> dict:
        return {"vault": tmp, "auto_create_projects": False, "exclude_folders": []}

    def _rules(self) -> dict:
        return rules_mod.load_rules(ROOT / "tracker" / "rules.yaml")

    def test_unmatched_assignment_becomes_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            _triggers, unmatched, pending, _matched = collect_trigger_dates(
                self._cfg(tmp), self._rules(), [], matcher.build_index([]), [sample_email()], {}, True
            )
            self.assertEqual(len(pending), 1)
            entry = pending["m1"]
            self.assertEqual(entry["status"], "new")
            self.assertEqual(entry["parsed"]["project_no"], "0207999")
            self.assertEqual(entry["parsed"]["so"], ["SO-123456"])
            self.assertEqual(entry["sharepoint"], "https://example.com/p")
            self.assertEqual(entry["checklist"], ["One", "Two"])
            self.assertEqual(entry["suggested_folder"], "Foo SO-123456 0207999")
            self.assertEqual(len(unmatched), 1)
            self.assertEqual(unmatched[0]["message_id"], "m1")

    def test_dismissed_stays_dismissed(self):
        with tempfile.TemporaryDirectory() as tmp:
            entry = projects_mod.pending_entry(
                sample_email(),
                matcher.parse_assignment_subject(sample_email().subject),
                extract.parse_assignment_body(sample_email().body_text),
            )
            entry["status"] = "dismissed"
            _t, _u, pending, _m = collect_trigger_dates(
                self._cfg(tmp),
                self._rules(),
                [],
                matcher.build_index([]),
                [sample_email()],
                {"pending": {"m1": entry}},
                True,
            )
            self.assertEqual(pending["m1"]["status"], "dismissed")

    def test_matched_project_removes_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Project(
                folder=str(Path(tmp) / "Foo SO-123456 0207999"),
                name="Foo SO-123456 0207999",
                project_no="0207999",
                so=["SO-123456"],
            )
            entry = projects_mod.pending_entry(
                sample_email(),
                matcher.parse_assignment_subject(sample_email().subject),
                extract.parse_assignment_body(sample_email().body_text),
            )
            _t, _u, pending, _m = collect_trigger_dates(
                self._cfg(tmp),
                self._rules(),
                [project],
                matcher.build_index([project]),
                [sample_email()],
                {"pending": {"m1": entry}},
                True,
            )
            self.assertEqual(pending, {})


class CreateProjectTests(unittest.TestCase):
    def _cfg(self, tmp: str) -> dict:
        xlsx = Path(tmp) / "TimeEstimates.xlsx"
        xlsx.write_bytes(b"xlsx")
        vault = Path(tmp) / "vault"
        vault.mkdir()
        return {
            "vault": str(vault),
            "templates_dir": str(ROOT / "templates"),
            "time_estimate_template": str(xlsx),
            "holidays": [],
            "rules_path": str(ROOT / "tracker" / "rules.yaml"),
        }

    def _entry(self) -> dict:
        email = Email(
            message_id="exact",
            subject=(
                "Project Assignment - Exact | SO-296266 | 0207840 | "
                "STAR23 w/ HHS Adapter, Decapper, PlateLoc, Centrifuge | L2"
            ),
            sender="APE admin",
            to="",
            cc="",
            date=datetime(2026, 11, 6, 10, 1, tzinfo=timezone.utc),
            body_text=(
                "Cindy, this project has been assigned to Samin Chowdhury and Anthony Zunino.\r\n\r\n"
                "Sharepoint Link <https://example.com/exact> \r\n\r\n"
                "AE Order Review Checklist:\r\n\u2610 Review the pipettor configuration\r\n\u2610 Confirm all custom parts\r\n"
            ),
        )
        return projects_mod.pending_entry(
            email,
            matcher.parse_assignment_subject(email.subject),
            extract.parse_assignment_body(email.body_text),
        )

    def test_create_from_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._cfg(tmp)
            entry = self._entry()
            info = projects_mod.create_from_pending(cfg, entry)
            folder = Path(info["folder"])
            note_path = folder / "Exact SO-296266 0207840.md"
            self.assertTrue(note_path.is_file())
            text = note_path.read_text(encoding="utf-8")
            for heading in ("Pre-Fabrication", "System Test", "Closeout", "Completion"):
                self.assertIn("**%s**" % heading, text)
            self.assertEqual(text.count("- [ ] Review the pipettor configuration"), 1)
            self.assertEqual(text.count("- [ ] Confirm all custom parts"), 1)
            self.assertIn("sharepoint: \"https://example.com/exact\"", text)
            self.assertTrue((folder / "06NOV2026_TimeEstimates.xlsx").is_file())
            self.assertEqual(len(info["files_created"]), 2)
            self.assertEqual(entry["status"], "created")

    def test_refuses_existing_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._cfg(tmp)
            entry = self._entry()
            projects_mod.create_from_pending(cfg, entry)
            with self.assertRaises(ValueError):
                projects_mod.create_from_pending(cfg, entry)

    def test_refuses_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._cfg(tmp)
            with self.assertRaises(ValueError):
                projects_mod.create_from_pending(cfg, self._entry(), folder_name="..\\x")


class DueOverrideTests(unittest.TestCase):
    def _tasks(self) -> list[Task]:
        return [
            Task(id="p1-a", title="A", stage="Pre-Fabrication", due=date(2026, 11, 10)),
            Task(id="p1-b", title="B", stage="Pre-Fabrication", due=date(2026, 11, 10)),
            Task(id="p1-c", title="C", stage="Pre-Fabrication", due=date(2026, 11, 10)),
        ]

    def test_override_and_manual_edit(self):
        tasks = self._tasks()
        entries = {
            "p1-a": {"mark": " ", "due": date(2026, 11, 20), "done_date": None, "line": "- [ ] A"},
            "p1-c": {"mark": "x", "due": date(2026, 12, 24), "done_date": None, "line": "- [x] C"},
        }
        overrides = {"p1-b": {"due": "2026-12-01", "note": "waiting on ACE"}}
        computed = {"p1-a": "2026-11-10", "p1-b": "2026-11-10", "p1-c": "2026-11-10"}
        dues = apply_due_overrides(tasks, entries, overrides, computed)
        self.assertEqual(dues, {"p1-a": "2026-11-10", "p1-b": "2026-11-10", "p1-c": "2026-11-10"})
        self.assertEqual(tasks[0].due, date(2026, 11, 20))
        self.assertEqual(overrides["p1-a"], {"due": "2026-11-20", "note": ""})
        self.assertEqual(tasks[1].due, date(2026, 12, 1))
        self.assertEqual(tasks[1].note, "waiting on ACE")
        self.assertEqual(tasks[2].due, date(2026, 11, 10))
        self.assertNotIn("p1-c", overrides)
        self.assertEqual(
            notes.format_task_line(tasks[1], None, date(2026, 11, 6)),
            "- [ ] B (waiting on ACE) \U0001F4C5 2026-12-01 \U0001F194 p1-b",
        )


class EmailOpenInfoTests(unittest.TestCase):
    def test_unmatched_entry_carries_open_info(self):
        email = sample_email()
        email.entry_id = "entry-1"
        entry = unmatched_entry(date(2026, 11, 6), email, "no trigger", {"so": [], "qte": [], "project_no": []})
        self.assertEqual(entry["open"], {"kind": "outlook", "entry_id": "entry-1"})
        eml = sample_email()
        eml.source_path = "C:/mail/drop/assignment.eml"
        self.assertEqual(
            unmatched_entry(date(2026, 11, 6), eml, "no trigger", {})["open"],
            {"kind": "eml", "path": "C:/mail/drop/assignment.eml"},
        )
        self.assertEqual(unmatched_entry(date(2026, 11, 6), sample_email(), "no trigger", {})["open"], {})

    def test_build_snapshot_email_open_info(self):
        email = sample_email()
        email.entry_id = "entry-1"
        project = Project(
            folder="p1", name="Foo SO-123456 0207999", project_no="0207999", so=["SO-123456"]
        )
        snapshot = build_snapshot(
            date(2026, 11, 6),
            [project],
            {},
            {project.key: [(email, "assignment", False)]},
            {},
            {},
            [],
            "gen",
        )
        self.assertEqual(
            snapshot["projects"][0]["emails"][0]["open"], {"kind": "outlook", "entry_id": "entry-1"}
        )


if __name__ == "__main__":
    unittest.main()
