from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tracker.hub import tasks

TODAY = date(2026, 11, 11)
NOTE = """---
project_no: "0207000"
---

**My tasks**
- [ ] Buy parts \U0001F4C5 2026-11-20 \U0001F194 m-aaaa1111
    - \U0001F4DD waiting on quote
    - \U0001F4DD second line
- [x] Done thing \u2705 2026-11-01 \U0001F194 m-bbbb2222

<!-- tracker:start (auto-managed; tick boxes freely) -->
**Pre-Fabrication**
- [ ] Review CPR/SO \U0001F4C5 2026-11-02 \U0001F194 p0207000-review_cpr_so
<!-- tracker:end -->
"""


class StatusTests(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(tasks.status_for("-", None, TODAY, 7), "not-needed")
        self.assertEqual(tasks.status_for("x", date(2020, 1, 1), TODAY, 7), "done")
        self.assertEqual(tasks.status_for(" ", None, TODAY, 7), "open")
        self.assertEqual(tasks.status_for(" ", date(2026, 11, 10), TODAY, 7), "overdue")
        self.assertEqual(tasks.status_for(" ", date(2026, 11, 11), TODAY, 7), "due-soon")
        self.assertEqual(tasks.status_for(" ", date(2026, 11, 18), TODAY, 7), "due-soon")
        self.assertEqual(tasks.status_for(" ", date(2026, 11, 19), TODAY, 7), "upcoming")


class ParseTests(unittest.TestCase):
    def test_metadata_block_and_notes(self):
        rows = tasks.parse_lines(NOTE)
        self.assertEqual(len(rows), 3)
        first = rows[0]
        self.assertEqual(first["mark"], " ")
        self.assertEqual(first["id"], "m-aaaa1111")
        self.assertEqual(first["due"], date(2026, 11, 20))
        self.assertEqual(first["title"], "Buy parts")
        self.assertFalse(first["in_block"])
        self.assertEqual(first["notes"], "waiting on quote second line")
        second = rows[1]
        self.assertEqual(second["mark"], "x")
        self.assertIsNone(second["due"])
        self.assertEqual(second["title"], "Done thing")
        self.assertEqual(second["notes"], "")
        third = rows[2]
        self.assertTrue(third["in_block"])
        self.assertEqual(third["id"], "p0207000-review_cpr_so")
        self.assertEqual(third["title"], "Review CPR/SO")

    def test_crlf_and_uppercase_mark(self):
        rows = tasks.parse_lines("- [X] Thing \U0001F4C5 2026-11-20\r\n")
        self.assertEqual(rows[0]["mark"], "x")
        self.assertEqual(rows[0]["title"], "Thing")


class InsertTests(unittest.TestCase):
    def test_heading_absent_with_block(self):
        text = "**TO DO:**\n\n<!-- tracker:start (auto-managed; tick boxes freely) -->\n<!-- tracker:end -->\n"
        line = tasks.manual_line("Buy parts", date(2026, 11, 20), "m-1111")
        new = tasks.insert_manual(text, [line])
        lines = new.split("\n")
        heading = lines.index("**My tasks**")
        block = next(i for i, item in enumerate(lines) if item.strip().startswith("<!-- tracker:start"))
        self.assertEqual(heading, block - 3)
        self.assertEqual(lines[heading + 1], line)
        self.assertEqual(lines[block - 1], "")

    def test_heading_present_appends_after_last_task(self):
        line = tasks.manual_line("New thing", None, "m-2222")
        new = tasks.insert_manual(NOTE, [line])
        lines = new.split("\n")
        self.assertEqual(lines.count("**My tasks**"), 1)
        self.assertEqual(lines[lines.index("**My tasks**") + 5], line)
        self.assertIn("- [x] Done thing", new)

    def test_no_block_appends_at_end(self):
        text = "---\nproject_no: \"1\"\n---\n\nsome body\n"
        line = tasks.manual_line("New thing", None, "m-3333")
        new = tasks.insert_manual(text, [line])
        self.assertTrue(new.startswith('---\nproject_no: "1"\n---\n\nsome body\n\n**My tasks**\n'))
        self.assertTrue(new.endswith(line + "\n"))

    def test_frontmatter_untouched(self):
        line = tasks.manual_line("New thing", None, "m-4444")
        new = tasks.insert_manual(NOTE, [line])
        self.assertTrue(new.startswith(NOTE[: NOTE.index("---\n\n**My tasks**") + 5]))

    def test_never_inside_block(self):
        line = tasks.manual_line("New thing", None, "m-5555")
        new = tasks.insert_manual(NOTE, [line])
        lines = new.split("\n")
        start = next(i for i, item in enumerate(lines) if item.strip().startswith("<!-- tracker:start"))
        end = lines.index("<!-- tracker:end -->")
        self.assertNotIn(line, lines[start:end])


class UpdateDeleteTests(unittest.TestCase):
    def test_update_keeps_mark_and_replaces_notes(self):
        new = tasks.update_manual(NOTE, "m-aaaa1111", description="Buy more parts", due=date(2026, 12, 1), notes="")
        self.assertIn("- [ ] Buy more parts \U0001F4C5 2026-12-01 \U0001F194 m-aaaa1111", new)
        self.assertNotIn("waiting on quote", new)
        self.assertIn("- [x] Done thing", new)

    def test_update_keeps_due_when_unset(self):
        new = tasks.update_manual(NOTE, "m-aaaa1111", description="Buy more parts")
        self.assertIn("\U0001F4C5 2026-11-20", new)

    def test_update_missing_id(self):
        with self.assertRaises(KeyError):
            tasks.update_manual(NOTE, "m-nope", description="x")

    def test_delete_removes_task_and_notes(self):
        new = tasks.delete_manual(NOTE, "m-aaaa1111")
        self.assertNotIn("m-aaaa1111", new)
        self.assertNotIn("waiting on quote", new)
        self.assertIn("- [x] Done thing", new)

    def test_delete_rejects_non_manual(self):
        with self.assertRaises(KeyError):
            tasks.delete_manual(NOTE, "p0207000-review_cpr_so")


class SetMarkTests(unittest.TestCase):
    def test_by_id(self):
        new, row = tasks.set_mark(NOTE, task_id="p0207000-review_cpr_so", mark="x")
        self.assertEqual(row["mark"], "x")
        self.assertIn("- [x] Review CPR/SO", new)
        self.assertEqual(len(new), len(NOTE))

    def test_by_id_missing(self):
        with self.assertRaises(KeyError):
            tasks.set_mark(NOTE, task_id="nope", mark="x")

    def test_by_index(self):
        lines = NOTE.split("\n")
        index = next(i for i, line in enumerate(lines) if "m-bbbb2222" in line)
        new, row = tasks.set_mark(NOTE, line_index=index, expected_line=lines[index], mark="-")
        self.assertEqual(row["mark"], "-")
        self.assertIn("- [-] Done thing", new)

    def test_by_index_conflict(self):
        with self.assertRaises(ValueError):
            tasks.set_mark(NOTE, line_index=0, expected_line="not the line", mark="x")

    def test_bad_mark(self):
        with self.assertRaises(ValueError):
            tasks.set_mark(NOTE, task_id="m-aaaa1111", mark="?")

    def test_new_manual_id(self):
        first = tasks.new_manual_id()
        self.assertTrue(first.startswith("m-"))
        self.assertEqual(len(first), 10)
        self.assertNotEqual(first, tasks.new_manual_id())


if __name__ == "__main__":
    unittest.main()
