from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tracker import extract
from tracker import rules as rules_mod
from tracker.models import Email, Project
from tracker.tracker import build_tasks, collect_timelines, collect_trigger_dates, merge_timelines, subject_project
from tracker import matcher

RULES = rules_mod.load_rules(ROOT / "tracker" / "rules.yaml")
TODAY = date(2026, 11, 20)


def table(*rows: tuple[str, str, str]) -> str:
    lines = ["Task Name", "Duration", "Start", "Finish"]
    for name, start, finish in rows:
        lines += [name, "1 day", start, finish]
    return "\n".join(lines)


KO_TABLE = table(
    ("Acme - SO-111111 Kickoff to Close Out", "Mon 9/14/26", "Fri 2/12/27"),
    ("Acme - SO-111111 PreFabApps", "Mon 9/14/26", "Fri 10/2/26"),
    ("Acme - SO-111111 Testing", "Mon 1/4/27", "Fri 1/29/27"),
    ("Acme - SO-111111 Pack Up", "Mon 2/1/27", "Wed 2/3/27"),
    ("Acme - SO-111111 AutoE Close Out", "Thu 2/4/27", "Fri 2/12/27"),
)
AT_TABLE = table(
    ("Acme - SO-111111 Testing", "Mon 12/7/26", "Fri 12/11/26"),
    ("Acme - SO-111111 Pack Up", "Fri 12/11/26", "Fri 12/11/26"),
    ("Acme - SO-111111 AutoE Close Out", "Wed 12/16/26", "Wed 12/16/26"),
)


def email(subject: str, body: str, sender: str, day: int, month: int = 11) -> Email:
    return Email(
        message_id="%s-%s-%d-%d" % (subject, sender, month, day),
        subject=subject,
        sender="%s </O=EXCHANGELABS/OU=X>" % sender,
        to="Cristian Uribe",
        cc="",
        date=datetime(2026, month, day, 15, tzinfo=timezone.utc),
        body_text=body,
    )


def project() -> Project:
    return Project(folder="x", name="SO-111111", project_no="0207001", so=["SO-111111"])


def merged(emails: list[Email], pm_name: str = "") -> dict[str, dict]:
    proj = project()
    tables = collect_timelines(RULES, [proj], emails, {}).get(proj.key) or []
    rows = merge_timelines(RULES, tables, pm_name)
    return {rules_mod.milestone_for(RULES, extract.strip_project_prefix(row["name"]))["id"]: row for row in rows}


class KoSubjectTests(unittest.TestCase):
    def classify(self, subject: str):
        return (rules_mod.classify(subject, RULES) or {}).get("id")

    def test_ko_with_project_id_is_kickoff(self):
        self.assertEqual(self.classify("SO-268679 - Pfizer KO Schedule"), "kickoff")
        self.assertEqual(self.classify("Acme QTE-056091 KO"), "kickoff")
        self.assertEqual(self.classify("KO - 0207001 Acme"), "kickoff")

    def test_ko_without_project_id_is_not_kickoff(self):
        self.assertIsNone(self.classify("Pfizer KO Schedule"))

    def test_ko_must_be_a_whole_word(self):
        self.assertIsNone(self.classify("SO-268679 KOALA order"))
        self.assertIsNone(self.classify("SO-268679 TOKO parts"))


class SummaryRowTests(unittest.TestCase):
    def test_summary_row_is_dropped(self):
        names = [row["name"] for row in extract.parse_schedule(KO_TABLE)]
        self.assertNotIn("Acme - SO-111111 Kickoff to Close Out", names)
        self.assertEqual(names[0], "Acme - SO-111111 PreFabApps")

    def test_trimmed_table_keeps_first_row(self):
        names = [row["name"] for row in extract.parse_schedule(AT_TABLE)]
        self.assertEqual(names[0], "Acme - SO-111111 Testing")
        self.assertEqual(len(names), 3)


class OwnTextTests(unittest.TestCase):
    def test_outlook_header_cuts_quote(self):
        body = "Updated dates below.\n\nFrom: Chris Derby\nSent: Monday\nTo: Team\nSubject: KO\n\nold text"
        self.assertEqual(extract.own_text(body).strip(), "Updated dates below.")

    def test_separator_and_wrote_lines_cut_quote(self):
        self.assertEqual(extract.own_text("new\n________________\nold").strip(), "new")
        self.assertEqual(extract.own_text("new\nOn Mon, Nov 16, 2026 Chris Derby wrote:\nold").strip(), "new")
        self.assertEqual(extract.own_text("new\n-----Original Message-----\nold").strip(), "new")

    def test_from_line_without_header_is_kept(self):
        body = "From: the PM's notes\nkeep this"
        self.assertIn("keep this", extract.own_text(body))


class MergeTimelineTests(unittest.TestCase):
    def test_newer_partial_table_updates_only_its_milestones(self):
        rows = merged([
            email("Acme SO-111111 Kickoff Notification", KO_TABLE, "Chris Derby", 14, 9),
            email("A&T Notification - SO-111111 Acme", AT_TABLE, "Chris Derby", 16),
        ])
        self.assertEqual(rows["ms_prefab"]["finish"], "2026-10-02")
        self.assertEqual(rows["ms_prefab"]["source"], "KO 9/14")
        self.assertEqual(rows["ms_testing"]["finish"], "2026-12-11")
        self.assertEqual(rows["ms_testing"]["source"], "A&T 11/16")
        self.assertEqual(rows["ms_closeout"]["finish"], "2026-12-16")

    def test_pm_reply_counts_only_above_the_quote(self):
        reply_body = AT_TABLE + "\n\nFrom: Someone\nSent: Monday\nTo: Team\n\n" + KO_TABLE
        rows = merged([
            email("Acme SO-111111 Kickoff Notification", KO_TABLE, "Chris Derby", 14, 9),
            email("RE: Acme SO-111111 Kickoff Notification", reply_body, "Chris Derby", 13),
        ])
        self.assertEqual(rows["ms_testing"]["finish"], "2026-12-11")
        self.assertEqual(rows["ms_testing"]["source"], "Reply 11/13")
        self.assertEqual(rows["ms_prefab"]["source"], "KO 9/14")

    def test_teammate_quoting_old_table_is_ignored(self):
        rows = merged([
            email("Acme SO-111111 Kickoff Notification", AT_TABLE, "Chris Derby", 14, 9),
            email("RE: Acme SO-111111 Kickoff Notification", KO_TABLE, "Jim Grieser", 13),
        ])
        self.assertEqual(rows["ms_testing"]["finish"], "2026-12-11")
        self.assertNotIn("ms_prefab", rows)

    def test_sheet_pm_is_trusted_without_kickoff(self):
        rows = merged([email("RE: SO-111111 schedule", AT_TABLE, "Scott Thompson", 13)], pm_name="Scott Thompson")
        self.assertEqual(rows["ms_testing"]["source"], "Reply 11/13")
        self.assertEqual(merged([email("RE: SO-111111 schedule", AT_TABLE, "Scott Thompson", 13)]), {})

    def test_rows_for_another_project_are_ignored(self):
        other = AT_TABLE.replace("SO-111111", "SO-999999")
        rows = merged([email("Acme SO-111111 Kickoff Notification", other, "Chris Derby", 14, 9)])
        self.assertEqual(rows, {})


class TableMismatchTests(unittest.TestCase):
    def collect(self, proj: Project, emails: list[Email]) -> tuple[list[dict], list[str]]:
        skipped: list[str] = []
        tables = collect_timelines(RULES, [proj], emails, {}, "", skipped).get(proj.key) or []
        return tables, skipped

    def test_wrong_table_under_subject_changes_no_dates_and_is_logged(self):
        # Wayne State case: subject names this project, table is another customer's SO.
        wrong = KO_TABLE.replace("Acme - SO-111111", "Amazon - SO-283465")
        tables, skipped = self.collect(project(), [
            email("Acme SO-111111 Kickoff Notification", KO_TABLE, "Chris Derby", 14, 9),
            email("Acme SO-111111 Kickoff Notification", wrong, "Chris Derby", 10, 10),
        ])
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0]["date"], date(2026, 9, 14))
        self.assertEqual(len(skipped), 1)
        self.assertIn("timeline skipped", skipped[0])
        self.assertIn("SO-111111", skipped[0])
        self.assertIn("SO-283465", skipped[0])

    def test_subject_so_not_in_folder_still_matches(self):
        # Neogenomics case: folder has SO-288125/177, table uses SO-288184 (listed in the subject).
        proj = Project(folder="n", name="SO-288125", so=["SO-288125", "SO-288177"])
        body = KO_TABLE.replace("Acme - SO-111111", "Neogenomics - SO-288184")
        tables, skipped = self.collect(proj, [
            email("Neogenomics SO-288184, SO-288125 & SO-288177 Kickoff Notification", body, "Scott Thompson", 22, 9),
        ])
        self.assertEqual(skipped, [])
        self.assertEqual(len(tables), 1)
        self.assertTrue(any("Testing" in row["name"] for row in tables[0]["rows"]))

    def test_one_matching_so_is_enough(self):
        # Pfizer case: several SOs on one project; the table names all three.
        proj = Project(folder="p", name="SO-285973", so=["SO-285973"])
        body = KO_TABLE.replace("Acme - SO-111111", "Pfizer - SO-285973; SO-285761; SO-285709")
        tables, skipped = self.collect(proj, [
            email("Pfizer - SO-285973; SO-285761; SO-285709 - New STAR Kickoff", body, "Chris Derby", 24, 9),
        ])
        self.assertEqual(skipped, [])
        self.assertEqual(len(tables[0]["rows"]), 4)

    def test_customer_name_difference_is_not_a_mismatch(self):
        # AstraZeneca subject, Medimmune rows, same SO.
        proj = Project(folder="m", name="SO-290635", so=["SO-290635"])
        body = KO_TABLE.replace("Acme - SO-111111", "Medimmune - SO-290635")
        tables, skipped = self.collect(proj, [
            email("AstraZeneca SO-290635 Kickoff Notification", body, "Scott Thompson", 26, 10),
        ])
        self.assertEqual(skipped, [])
        self.assertEqual(len(tables), 1)

    def test_previously_so_does_not_move_the_old_project_stage(self):
        old = Project(folder="c", name="SO-268726", so=["SO-268726"])
        new = Project(folder="a", name="SO-283465", so=["SO-283465"])
        mail = email("Amazon - SO-283465 Upgrade (Previously SO-268726) Kickoff Notification", "", "Chris Derby", 10, 9)
        triggers, _u, _p, _m = collect_trigger_dates(
            {"me": {}}, RULES, [old, new], matcher.build_index([old, new]), [mail], {}, True
        )
        self.assertIn("kickoff", triggers.get(new.key) or {})
        self.assertNotIn(old.key, triggers)

    def test_table_without_ids_is_accepted(self):
        body = table(("PreFabApps", "9/14/26", "10/2/26"), ("Testing", "1/4/27", "1/29/27"))
        tables, skipped = self.collect(project(), [email("Acme SO-111111 Kickoff", body, "Chris Derby", 14, 9)])
        self.assertEqual(skipped, [])
        self.assertEqual(len(tables), 1)

    def test_first_id_in_subject_picks_the_project(self):
        old = Project(folder="y", name="SO-222222", so=["SO-222222"])
        index = matcher.build_index([old, project()])
        self.assertEqual(subject_project("Acme - SO-111111 Upgrade (Previously SO-222222)", index).key, project().key)

    def test_build_tasks_carries_the_source(self):
        schedule = merge_timelines(RULES, collect_timelines(RULES, [project()], [
            email("A&T Notification - SO-111111 Acme", AT_TABLE, "Chris Derby", 16),
        ], {}).get(project().key) or [], "Chris Derby")
        tasks = {task.id.split("-", 1)[1]: task for task in build_tasks(
            project(), {"kickoff": date(2026, 9, 10)}, RULES, [], TODAY, schedule, level="L2")[0]}
        self.assertEqual(tasks["ms_testing"].due, date(2026, 12, 11))
        self.assertEqual(tasks["ms_testing"].due_source, "A&T 11/16")
        children = [task for task in tasks.values() if task.parent == tasks["ms_testing"].id]
        self.assertTrue(children)
        self.assertTrue(all(task.due_source == "A&T 11/16" for task in children))


if __name__ == "__main__":
    unittest.main()
