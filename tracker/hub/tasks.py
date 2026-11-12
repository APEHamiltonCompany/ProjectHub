from __future__ import annotations

import calendar
import re
import secrets
from datetime import date, timedelta

from tracker import matcher, notes

MANUAL_PREFIX = "m-"
NOTES_EMOJI = "\U0001F4DD"
RECUR_EMOJI = "\U0001f501"
HOURS_EMOJI = "\u23f1"
HEADING = "**My tasks**"
BLOCK_START_PREFIX = "<!-- tracker:start"
DUE_EMOJI = notes.DUE_EMOJI
ID_EMOJI = notes.ID_EMOJI
CHECK_RE = re.compile(r"^(\s*[-*]\s+\[)([ xX-])(\]\s.*)$")
NOTES_RE = re.compile(r"^\s*[-*]\s+" + NOTES_EMOJI + r"\s*(.*)$")
ID_RE = notes.ID_RE
DUE_RE = notes.DUE_RE
META_EMOJI = (
    notes.DUE_EMOJI,
    "\u23f3",
    "\U0001f6eb",
    "\u2795",
    notes.DONE_EMOJI,
    "\u274c",
    RECUR_EMOJI,
    notes.ID_EMOJI,
    "\u26d4",
    "\U0001f3c1",
    "\U0001f9f9",
    HOURS_EMOJI + "\ufe0f",
    HOURS_EMOJI,
)
META_RE = re.compile("(?:" + "|".join(re.escape(item) for item in META_EMOJI) + r")\s*\S*")
HOURS_RE = re.compile(HOURS_EMOJI + r"\ufe0f?\s*(\d+(?:\.\d+)?)h")
HOURS_STRIP_RE = re.compile(r"\s*" + HOURS_EMOJI + r"\ufe0f?\s*\d+(?:\.\d+)?h")
DAYS_RE = re.compile(r"\[days::\s*([^\]]*)\]")
DAYS_STRIP_RE = re.compile(r"\s*\[days::\s*[^\]]*\]")
REF_RE = re.compile(r"\[ref::\s*([^\]]*)\]")
REF_PATTERN = re.compile(r"^(SO|QTE)-\d+$")
TOKEN_START_RE = re.compile(
    "|".join(re.escape(item) for item in META_EMOJI if not item.startswith(HOURS_EMOJI))
)
MAX_HOURS = 24 * 7
RECUR_RE = re.compile(
    RECUR_EMOJI
    + r"\s*(.+?)(?=\s*(?:"
    + "|".join(re.escape(item) for item in META_EMOJI)
    + r")|\s+#|$)"
)
CATEGORIES = [
    ("project", "Project"),
    ("personal", "Personal"),
    ("admin", "Admin"),
    ("process-improvement", "Process improvement"),
    ("unassigned-project", "Unassigned project"),
    ("other", "Other"),
]
CATEGORY_IDS = tuple(item[0] for item in CATEGORIES)
CATEGORY_LABELS = dict(CATEGORIES)
TAG_CATEGORIES = tuple(item[0] for item in CATEGORIES if item[0] != "project")
CATEGORY_RE = re.compile(
    "#(" + "|".join(sorted(TAG_CATEGORIES, key=len, reverse=True)) + r")(?![-\w])"
)
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
RULE_DAY_RE = re.compile(r"^every day$", re.I)
RULE_WEEKDAY_RE = re.compile(r"^every weekday$", re.I)
RULE_WEEK_RE = re.compile(r"^every week on (" + "|".join(WEEKDAYS) + r")$", re.I)
RULE_NWEEK_RE = re.compile(r"^every (\d+) weeks on (" + "|".join(WEEKDAYS) + r")$", re.I)
RULE_MONTH_RE = re.compile(r"^every month on the (\d+)(?:st|nd|rd|th)$", re.I)
UNSET = object()


def status_for(mark: str, due: date | None, today: date, soon_days: int) -> str:
    if mark == "-":
        return "not-needed"
    if mark == "x":
        return "done"
    if due is None:
        return "open"
    if due < today:
        return "overdue"
    if (due - today).days <= soon_days:
        return "due-soon"
    return "upcoming"


def parse_rule(text) -> dict | None:
    if isinstance(text, dict):
        return dict(text)
    rule = re.sub(r"\s+", " ", str(text or "")).strip()
    if not rule:
        return None
    if RULE_DAY_RE.match(rule):
        return {"kind": "day"}
    if RULE_WEEKDAY_RE.match(rule):
        return {"kind": "weekday"}
    match = RULE_WEEK_RE.match(rule)
    if match:
        return {"kind": "week", "weeks": 1, "weekday": WEEKDAYS.index(match.group(1).capitalize())}
    match = RULE_NWEEK_RE.match(rule)
    if match:
        weeks = int(match.group(1))
        if not 2 <= weeks <= 12:
            return None
        return {"kind": "week", "weeks": weeks, "weekday": WEEKDAYS.index(match.group(2).capitalize())}
    match = RULE_MONTH_RE.match(rule)
    if match:
        day = int(match.group(1))
        if not 1 <= day <= 31:
            return None
        return {"kind": "month", "day": day}
    return None


def canonical_rule(text) -> str | None:
    rule = parse_rule(text)
    if rule is None:
        return None
    if rule["kind"] == "day":
        return "every day"
    if rule["kind"] == "weekday":
        return "every weekday"
    if rule["kind"] == "month":
        return "every month on the " + _ordinal(rule["day"])
    name = WEEKDAYS[rule["weekday"]]
    if rule["weeks"] == 1:
        return "every week on " + name
    return "every %d weeks on %s" % (rule["weeks"], name)


def short_label(rule) -> str:
    parsed = parse_rule(rule)
    if parsed is None:
        return ""
    if parsed["kind"] == "day":
        return "daily"
    if parsed["kind"] == "weekday":
        return "weekdays"
    if parsed["kind"] == "month":
        return "monthly"
    if parsed["weeks"] == 1:
        return "weekly"
    return "every %d weeks" % parsed["weeks"]


def next_due(rule, base: date, today: date) -> date:
    parsed = parse_rule(rule)
    if parsed is None:
        raise ValueError("bad rule")
    if parsed["kind"] == "day":
        result = base + timedelta(days=1)
        while result < today:
            result += timedelta(days=1)
        return result
    if parsed["kind"] == "weekday":
        result = _next_weekday(base + timedelta(days=1))
        while result < today:
            result = _next_weekday(result + timedelta(days=1))
        return result
    if parsed["kind"] == "week":
        step = 7 * parsed["weeks"]
        offset = (parsed["weekday"] - base.weekday()) % 7
        result = base + timedelta(days=offset or step)
        while result < today:
            result += timedelta(days=step)
        return result
    result = _month_step(base, parsed["day"])
    while result < today:
        result = _month_step(result, parsed["day"])
    return result


def first_due(rule, today: date) -> date:
    parsed = parse_rule(rule)
    if parsed is None:
        raise ValueError("bad rule")
    if parsed["kind"] == "day":
        return today
    if parsed["kind"] == "weekday":
        return _next_weekday(today)
    if parsed["kind"] == "week":
        return today + timedelta(days=(parsed["weekday"] - today.weekday()) % 7)
    result = date(today.year, today.month, min(parsed["day"], _month_len(today.year, today.month)))
    if result < today:
        result = _month_step(result, parsed["day"])
    return result


def parse_lines(text: str) -> list[dict]:
    lines = text.replace("\r\n", "\n").split("\n")
    rows: list[dict] = []
    in_block = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(BLOCK_START_PREFIX):
            in_block = True
            continue
        if stripped == notes.BLOCK_END:
            in_block = False
            continue
        match = CHECK_RE.match(line)
        if not match:
            continue
        ident = ID_RE.search(line)
        due = DUE_RE.search(line)
        parts: list[str] = []
        cursor = index + 1
        while cursor < len(lines):
            note_match = NOTES_RE.match(lines[cursor])
            if not note_match:
                break
            parts.append(note_match.group(1).strip())
            cursor += 1
        title = RECUR_RE.sub(" ", match.group(3)[1:])
        title = CATEGORY_RE.sub(" ", title)
        title = META_RE.sub(" ", title)
        title = DAYS_RE.sub(" ", title)
        title = REF_RE.sub(" ", title)
        tag = CATEGORY_RE.search(match.group(3))
        recur = RECUR_RE.search(match.group(3))
        hours = HOURS_RE.search(line)
        days = DAYS_RE.search(line)
        ref = REF_RE.search(line)
        rows.append(
            {
                "index": index,
                "line": line,
                "mark": match.group(2).lower(),
                "id": ident.group(1) if ident else "",
                "due": date.fromisoformat(due.group(1)) if due else None,
                "title": re.sub(r"\s+", " ", title).strip(),
                "in_block": in_block,
                "notes": " ".join(part for part in parts if part),
                "category": tag.group(1) if tag else "",
                "recur": (canonical_rule(recur.group(1)) or "") if recur else "",
                "hours": float(hours.group(1)) if hours else None,
                "days": _parse_days(days.group(1)) if days else [],
                "ref": ref.group(1).strip().upper() if ref else "",
            }
        )
    return rows


def _parse_days(raw: str) -> list[date]:
    values = []
    for part in str(raw or "").split(","):
        text = part.strip()
        if not text:
            continue
        try:
            values.append(date.fromisoformat(text))
        except ValueError:
            continue
    return sorted(set(values))


def manual_line(
    description: str,
    due: date | None,
    task_id: str,
    category: str | None = None,
    recur: str | None = None,
    ref: str | None = None,
) -> str:
    return "- [ ] " + _line_body(description, due, task_id, category=category, recur=recur, ref=ref)


def notes_line(notes_text: str) -> str:
    return "    - " + NOTES_EMOJI + " " + notes_text


def insert_manual(text: str, task_lines: list[str]) -> str:
    header, body = _split(text)
    lines = body.split("\n")
    heading_index = None
    for index, line in enumerate(lines):
        if line.strip() == HEADING:
            heading_index = index
            break
    if heading_index is not None:
        insert_at = heading_index + 1
        while insert_at < len(lines) and (CHECK_RE.match(lines[insert_at]) or NOTES_RE.match(lines[insert_at])):
            insert_at += 1
        lines = lines[:insert_at] + list(task_lines) + lines[insert_at:]
        return header + "\n".join(lines)
    start_index = None
    for index, line in enumerate(lines):
        if line.strip().startswith(BLOCK_START_PREFIX):
            start_index = index
            break
    if start_index is not None:
        chunk = [HEADING] + list(task_lines)
        if start_index > 0 and lines[start_index - 1].strip() != "":
            chunk = [""] + chunk
        chunk = chunk + [""]
        lines = lines[:start_index] + chunk + lines[start_index:]
        return header + "\n".join(lines)
    if lines and lines[-1].strip() != "":
        lines = lines + [""]
    lines = lines + [HEADING] + list(task_lines) + [""]
    return header + "\n".join(lines)


def update_manual(
    text: str,
    task_id: str,
    description: str | None = None,
    due=UNSET,
    notes: str | None = None,
    category=UNSET,
    recur=UNSET,
    ref=UNSET,
) -> str:
    lines = text.split("\n")
    index = _find_manual(lines, task_id)
    match = CHECK_RE.match(lines[index])
    row = _row_at(lines, index)
    new_description = row["title"] if description is None else description
    new_due = row["due"] if due is UNSET else due
    new_category = row["category"] if category is UNSET else category
    new_recur = row["recur"] if recur is UNSET else recur
    new_ref = row["ref"] if ref is UNSET else ref
    lines[index] = match.group(1) + match.group(2) + "] " + _line_body(
        new_description, new_due, task_id, category=new_category, recur=new_recur, ref=new_ref or None
    )
    if row["hours"] or row["days"]:
        lines[index] = _apply_time(lines[index], row["hours"], row["days"])
    if notes is not None:
        end = _notes_end(lines, index)
        lines[index + 1:end] = [notes_line(notes)] if notes else []
    return "\n".join(lines)


def delete_manual(text: str, task_id: str) -> str:
    lines = text.split("\n")
    index = _find_manual(lines, task_id)
    del lines[index:_notes_end(lines, index)]
    return "\n".join(lines)


def set_mark(
    text: str,
    task_id: str | None = None,
    line_index: int | None = None,
    expected_line: str | None = None,
    mark: str = " ",
) -> tuple[str, dict]:
    if mark not in (" ", "x", "-"):
        raise ValueError("bad mark")
    lines = text.replace("\r\n", "\n").split("\n")
    if task_id:
        index = None
        for cursor, line in enumerate(lines):
            if not CHECK_RE.match(line):
                continue
            ident = ID_RE.search(line)
            if ident and ident.group(1) == task_id:
                index = cursor
                break
        if index is None:
            raise KeyError(task_id)
    else:
        if line_index is None or line_index < 0 or line_index >= len(lines):
            raise ValueError("conflict")
        if expected_line is None or lines[line_index] != expected_line:
            raise ValueError("conflict")
        index = line_index
    match = CHECK_RE.match(lines[index])
    if not match:
        raise ValueError("conflict")
    lines[index] = match.group(1) + mark + match.group(3)
    new_text = "\n".join(lines)
    return new_text, _row_at(lines, index)


def set_time(
    text: str,
    task_id: str | None = None,
    line_index: int | None = None,
    expected_line: str | None = None,
    hours=None,
    days=None,
) -> tuple[str, dict]:
    value = None
    if hours not in (None, ""):
        try:
            value = float(hours)
        except (TypeError, ValueError):
            raise ValueError("bad hours")
        if value == 0:
            value = None
        elif not 0 < value <= MAX_HOURS:
            raise ValueError("bad hours")
    day_list: list[date] = []
    for item in days or ():
        try:
            day_list.append(date.fromisoformat(str(item).strip()))
        except ValueError:
            raise ValueError("bad days")
    day_list = sorted(set(day_list))
    lines = text.replace("\r\n", "\n").split("\n")
    if task_id:
        index = None
        for cursor, line in enumerate(lines):
            if not CHECK_RE.match(line):
                continue
            ident = ID_RE.search(line)
            if ident and ident.group(1) == task_id:
                index = cursor
                break
        if index is None:
            raise KeyError(task_id)
    else:
        if line_index is None or line_index < 0 or line_index >= len(lines):
            raise ValueError("conflict")
        if expected_line is None or lines[line_index] != expected_line:
            raise ValueError("conflict")
        index = line_index
    if not CHECK_RE.match(lines[index]):
        raise ValueError("conflict")
    lines[index] = _apply_time(lines[index], value, day_list)
    new_text = "\n".join(lines)
    return new_text, _row_at(lines, index)


def suggest_ref(title: str, notes_text: str = "") -> str:
    keys = matcher.extract_keys("%s %s" % (title or "", notes_text or ""))
    for bucket in ("so", "qte"):
        values = sorted(keys.get(bucket) or ())
        if values:
            return values[0]
    return ""


def new_manual_id() -> str:
    return MANUAL_PREFIX + secrets.token_hex(4)


def spawn_next(text: str, task_id: str, today: date) -> tuple[str, dict | None]:
    lines = text.replace("\r\n", "\n").split("\n")
    index = _find_manual(lines, task_id)
    row = _row_at(lines, index)
    if row["mark"] not in ("x", "-") or not row["recur"]:
        return text, None
    due = next_due(row["recur"], row["due"] or today, today)
    for other in parse_lines(text):
        if other["mark"] != " " or not other["id"].startswith(MANUAL_PREFIX):
            continue
        if other["title"] == row["title"] and other["recur"] == row["recur"] and other["due"] == due:
            return text, None
    new_id = new_manual_id()
    new_line = "- [ ] " + _line_body(
        row["title"], due, new_id, category=row["category"] or None, recur=row["recur"], ref=row["ref"] or None
    )
    notes_lines = lines[index + 1:_notes_end(lines, index)]
    lines[index:index] = [new_line] + notes_lines
    return "\n".join(lines), _row_at(lines, index)


def _split(text: str) -> tuple[str, str]:
    _data, _raw, body = notes.split_frontmatter(text)
    return text[: len(text) - len(body)], body


def _line_body(
    description: str,
    due: date | None,
    task_id: str,
    category: str | None = None,
    recur: str | None = None,
    ref: str | None = None,
) -> str:
    parts = [description]
    if ref:
        parts.append("[ref:: " + ref + "]")
    if category and category != "project":
        parts.append("#" + category)
    if recur:
        parts.append(RECUR_EMOJI + " " + recur)
    if due is not None:
        parts.append(DUE_EMOJI + " " + due.isoformat())
    parts.append(ID_EMOJI + " " + task_id)
    return " ".join(parts)


def _apply_time(line: str, hours: float | None, days: list[date]) -> str:
    line = HOURS_STRIP_RE.sub("", line)
    line = DAYS_STRIP_RE.sub("", line)
    suffix = ""
    if hours:
        suffix += " " + HOURS_EMOJI + " " + _format_hours(hours)
    if days:
        suffix += " [days:: " + ", ".join(item.isoformat() for item in days) + "]"
    if not suffix:
        return line.rstrip()
    cut = _meta_start(line)
    return line[:cut].rstrip() + suffix + " " + line[cut:].lstrip() if cut < len(line) else line.rstrip() + suffix


def _meta_start(line: str) -> int:
    match = CHECK_RE.match(line)
    offset = len(match.group(1)) + 2 if match else 0
    positions = [len(line)]
    for regex in (TOKEN_START_RE, REF_RE, CATEGORY_RE):
        found = regex.search(line, offset)
        if found:
            positions.append(found.start())
    return min(positions)


def _format_hours(value: float) -> str:
    return "%gh" % value


def _ordinal(value: int) -> str:
    if 11 <= value % 100 <= 13:
        return "%dth" % value
    return "%d%s" % (value, {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th"))


def _month_len(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _month_step(base: date, day: int) -> date:
    year = base.year + (1 if base.month == 12 else 0)
    month = 1 if base.month == 12 else base.month + 1
    return date(year, month, min(day, _month_len(year, month)))


def _next_weekday(value: date) -> date:
    while value.weekday() >= 5:
        value += timedelta(days=1)
    return value


def _notes_end(lines: list[str], index: int) -> int:
    end = index + 1
    while end < len(lines) and NOTES_RE.match(lines[end]):
        end += 1
    return end


def _find_manual(lines: list[str], task_id: str) -> int:
    if not task_id.startswith(MANUAL_PREFIX):
        raise KeyError(task_id)
    for index, line in enumerate(lines):
        if not CHECK_RE.match(line):
            continue
        ident = ID_RE.search(line)
        if ident and ident.group(1) == task_id:
            return index
    raise KeyError(task_id)


def _row_at(lines: list[str], index: int) -> dict:
    for row in parse_lines("\n".join(lines)):
        if row["index"] == index:
            return row
    raise KeyError(index)
