from __future__ import annotations

import re
from datetime import date
from typing import Optional
from urllib.parse import unquote

ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\ufeff\u00a0]")
LINK_RE = re.compile(r"<((?:https?://)[^>\s]+)>")
DATE_TOKEN_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")

SHAREPOINT_RE = re.compile(r"Sharepoint\s+Link\s*<([^>]+)>", re.I)
ASSIGNED_RE = re.compile(r"assigned\s+to\s+([^.\n]+)", re.I)
GREETING_RE = re.compile(r"^\s*([A-Z][A-Za-z'\-]+)\s*,")
CHECK_HEADER_RE = re.compile(r"^\s*AE\s+Order\s+Review\s+Checklist\s*:?\s*$", re.I)
CHECK_STOP_RE = re.compile(r"^\s*ME\s+Order\s+Review\s+Checklist", re.I)
CHECK_ITEM_RE = re.compile(r"^\s*(?:\u2610|\[\s*\])\s*(.+)$")

PROJECT_PAGE_LABEL = r"Project\s+page"
CPR_LABEL = r"CPR(?:\s+form)?"
RMT_LABEL = r"Risk(?:s)?\s*(?:&|and)\s*Issues\s+document"
DR_FORM_LABEL = r"\b(?:Previous\s+)?DR(?:\s+form)?\b"
LEVEL_RE = re.compile(r"Project\s+level\s*:\s*(\S+)", re.I)
SHIP_DATE_RE = re.compile(r"Ship\s+date\s*:\s*(.+)", re.I)
DESIGN_NEED_BY_RE = re.compile(r"Design\s+need-by\s+date\s*:\s*(.+)", re.I)
CUSTOM_HEADER_RE = re.compile(r"^\s*Custom\s+parts\s*:?\s*$", re.I)
CUSTOM_STOP_RE = re.compile(r"^\s*(?:Project\s+timeline|Task\s+Name)\s*:?\s*$", re.I)
CUSTOM_SKIP_RE = re.compile(r"^(?:Part\s+Name|SK\s+Part\s*#?)$", re.I)
NUMBERING_RE = re.compile(r"^\d+\.\s*")

TIMELINE_RE = re.compile(r"^\s*Project\s+timeline\s*:?\s*$", re.I)
DURATION_RE = re.compile(r"^\d+(?:\.\d+)?\s+days?\??$")
TASK_NAME_RE = re.compile(r"^\s*Task\s+Name\s*$", re.I)
SCHEDULE_DATE_RE = re.compile(r"^(?:(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+)?(\d{1,2})/(\d{1,2})/(\d{2,4})$")
SO_PREFIX_RE = re.compile(r"^.+?\s-\s(?:SO|QTE)-\d{6}(?:\s*[,;&/]\s*(?:and\s+)?(?:SO|QTE)-\d{6})*\s+")


def clean(text: str) -> str:
    return ZERO_WIDTH_RE.sub("", text or "").replace("\r\n", "\n").replace("\r", "\n")


def parse_us_date(text: str) -> Optional[date]:
    match = DATE_TOKEN_RE.search(text or "")
    if not match:
        return None
    month, day, year = (int(group) for group in match.groups())
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _label_url(lines: list[str], label: str) -> str:
    regex = re.compile(label, re.I)
    for line in lines:
        match = regex.search(line)
        if not match:
            continue
        link = LINK_RE.search(line, match.end())
        if link:
            return link.group(1).strip()
    return ""


def _split_names(text: str) -> list[str]:
    names = [part.strip(" .") for part in re.split(r"\s*,\s*|\s+and\s+", text or "") if part.strip(" .")]
    return [name for name in names if name]


def _checklist(lines: list[str]) -> list[str]:
    items: list[str] = []
    started = False
    blanks = 0
    for line in lines:
        if not started:
            if CHECK_HEADER_RE.match(line):
                started = True
            continue
        if CHECK_STOP_RE.match(line):
            break
        if not line.strip():
            blanks += 1
            if blanks >= 2:
                break
            continue
        blanks = 0
        match = CHECK_ITEM_RE.match(line)
        if match:
            items.append(re.sub(r"\s+", " ", match.group(1)).strip())
    return items


def _custom_parts(lines: list[str]) -> list[str]:
    parts: list[str] = []
    started = False
    blanks = 0
    for line in lines:
        if not started:
            if CUSTOM_HEADER_RE.match(line):
                started = True
            continue
        if CUSTOM_STOP_RE.match(line):
            break
        if not line.strip():
            blanks += 1
            if blanks >= 2:
                break
            continue
        blanks = 0
        text = NUMBERING_RE.sub("", line.strip()).strip()
        if not text or CUSTOM_SKIP_RE.match(text) or not re.search(r"[A-Za-z0-9]", text):
            continue
        parts.append(re.sub(r"\s+", " ", text))
    return parts


def parse_assignment_body(body: str) -> dict:
    text = clean(body)
    lines = text.split("\n")
    sharepoint = ""
    match = SHAREPOINT_RE.search(text)
    if match:
        sharepoint = match.group(1).strip()
    else:
        sharepoint = _label_url(lines, r"Sharepoint\s+Link")
    team: list[str] = []
    match = ASSIGNED_RE.search(text)
    if match:
        team = _split_names(match.group(1))
    pm_first_name = ""
    for line in lines:
        if not line.strip():
            continue
        greeting = GREETING_RE.match(line)
        if greeting:
            pm_first_name = greeting.group(1)
        break
    return {
        "sharepoint": sharepoint,
        "team": team,
        "pm_first_name": pm_first_name,
        "checklist": _checklist(lines),
        "rmt_note": "RMT" in text,
    }


def _schedule_date(text: str) -> Optional[date]:
    match = SCHEDULE_DATE_RE.match((text or "").strip())
    if not match:
        return None
    month, day, year = int(match.group(2)), int(match.group(3)), int(match.group(4))
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_schedule(body: str, me_name: str = "") -> list[dict]:
    lines = clean(body).split("\n")
    start = None
    for index, line in enumerate(lines):
        if TIMELINE_RE.match(line):
            start = index + 1
            break
        if TASK_NAME_RE.match(line):
            following = [item.strip() for item in lines[index + 1:index + 4] if item.strip()]
            if following and following[0].lower() == "duration":
                start = index
                break
    if start is None:
        return []
    rows = [(index, line.strip()) for index, line in enumerate(lines[start:], start) if line.strip()]
    has_resources = any(line.lower().startswith("resource name") for _index, line in rows[:10])
    result: list[dict] = []
    position = 0
    while position < len(rows):
        name = rows[position][1]
        if position + 1 >= len(rows) or not DURATION_RE.match(rows[position + 1][1]):
            position += 1
            continue
        cursor = position + 2
        if cursor < len(rows) and _schedule_date(rows[cursor][1]) is None:
            cursor += 1
        if cursor + 1 >= len(rows):
            break
        start_date = _schedule_date(rows[cursor][1])
        finish_date = _schedule_date(rows[cursor + 1][1])
        if start_date is None or finish_date is None:
            position += 1
            continue
        cursor += 2
        resources = ""
        if has_resources and cursor < len(rows):
            candidate = rows[cursor][1]
            next_is_duration = cursor + 1 < len(rows) and bool(DURATION_RE.match(rows[cursor + 1][1]))
            if _schedule_date(candidate) is None and not next_is_duration:
                resources = candidate
                cursor += 1
        result.append(
            {
                "name": name,
                "start": start_date.isoformat(),
                "finish": finish_date.isoformat(),
                "resources": resources,
                "mine": bool(me_name) and me_name.lower() in resources.lower(),
            }
        )
        position = cursor
    if len(result) > 1 and _is_summary_row(result[0], result[1:]):
        result = result[1:]
    return result


def _is_summary_row(first: dict, rest: list[dict]) -> bool:
    """MS Project's project summary row spans every other row; a trimmed table that starts at a real task keeps it."""
    if len(rest) < 2:
        return False
    return first["start"] <= min(row["start"] for row in rest) and first["finish"] >= max(row["finish"] for row in rest)


QUOTE_START_RES = (
    re.compile(r"^\s*_{8,}\s*$"),
    re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}", re.I),
    re.compile(r"^\s*On .{6,200} wrote:\s*$", re.I),
)
QUOTE_FROM_RE = re.compile(r"^\s*From:\s*\S", re.I)
QUOTE_HEADER_RE = re.compile(r"^\s*(Sent|Date|To|Subject):", re.I)


def own_text(body: str) -> str:
    """The part of an email above the first quoted message (Outlook "From:/Sent:" header, separator line or "On ... wrote:")."""
    lines = clean(body).split("\n")
    for index, line in enumerate(lines):
        if any(pattern.match(line) for pattern in QUOTE_START_RES):
            return "\n".join(lines[:index])
        if QUOTE_FROM_RE.match(line):
            following = [item for item in lines[index + 1:index + 6] if item.strip()][:3]
            if any(QUOTE_HEADER_RE.match(item) for item in following):
                return "\n".join(lines[:index])
    return "\n".join(lines)


def parse_kickoff_body(body: str) -> dict:
    text = clean(body)
    lines = text.split("\n")
    level = ""
    match = LEVEL_RE.search(text)
    if match:
        value = match.group(1).strip().upper()
        level = value if re.fullmatch(r"L\d", value) else ("L" + value if value.isdigit() else value)
    ship_date_text = ""
    match = SHIP_DATE_RE.search(text)
    if match:
        ship_date_text = match.group(1).strip()
    design_text = ""
    match = DESIGN_NEED_BY_RE.search(text)
    if match:
        design_text = match.group(1).strip()
    ship_date = parse_us_date(ship_date_text)
    design_need_by = parse_us_date(design_text)
    cpr = _label_url(lines, CPR_LABEL)
    dr_form = _label_url(lines, DR_FORM_LABEL)
    if cpr and is_design_review_url(cpr):
        # Repeat projects: the "CPR form" line holds the previous design review instead.
        dr_form = dr_form or cpr
        cpr = ""
    return {
        "project_page": _label_url(lines, PROJECT_PAGE_LABEL),
        "cpr": cpr,
        "dr_form": dr_form,
        "rmt": _label_url(lines, RMT_LABEL),
        "level": level,
        "ship_date": ship_date.isoformat() if ship_date else None,
        "ship_date_text": ship_date_text,
        "design_need_by": design_need_by.isoformat() if design_need_by else None,
        "design_need_by_text": design_text,
        "custom_parts": _custom_parts(lines),
        "schedule": parse_schedule(text),
    }


def _link_title(prefix: str) -> str:
    text = ZERO_WIDTH_RE.sub(" ", prefix or "")
    words = text.split()[-8:]
    while words and words[-1].lower() in ("here", ":"):
        words.pop()
    title = " ".join(words).strip(" :-")
    return title or "link"


def extract_links(body: str) -> list[dict]:
    links: dict[str, dict] = {}
    for line in clean(body).split("\n"):
        for match in LINK_RE.finditer(line):
            url = match.group(1).strip()
            if url in links:
                continue
            links[url] = {"title": _link_title(line[: match.start()]), "url": url}
    return list(links.values())


def is_slides_url(url: str) -> bool:
    lowered = (url or "").lower()
    return "/:p:/" in lowered or ".pptx" in lowered


def is_design_review_url(url: str) -> bool:
    """A PowerPoint link, or a file named DesignReview / Design Review / DR-<number>."""
    if is_slides_url(url):
        return True
    name = re.sub(r"[\s_\-]+", "", unquote(url or "").lower())
    return "designreview" in name


def design_review_link(body: str) -> str:
    """The design review deck in a DR email: the slides link, preferring one named after a design review."""
    slides = [link for link in extract_links(body) if is_slides_url(link["url"])]
    for link in slides:
        if re.search(r"design[\s_-]*review|\bDR\b", link["title"] + " " + unquote(link["url"]), re.I):
            return link["url"]
    return slides[0]["url"] if slides else ""


PROJECT_NO_RE = re.compile(r"(?<!\d)020\d{4}(?!\d)")
REVISION_RE = re.compile(r"(?<![a-z])rev[\s._-]*([a-z])(?![a-z])((?:[\s._-]*\d+)*)", re.I)
DR_SUBJECT_RE = re.compile(r"(?i:design[\s_-]*review)|(?<![A-Za-z])DR(?![A-Za-z])")
DR_LABEL_RE = re.compile(r"(?:current\s+)?design[\s_-]*review(?:\s+(?:deck|slides|link))?", re.I)
SUPPORT_SUBJECT_RE = re.compile(r"applications\s+project\s+support", re.I)


def link_file_name(link: dict) -> str:
    """The file name in a SharePoint link (file= parameter or the last path part), else the text before the link."""
    url = unquote((link.get("url") or "").replace("+", " "))
    match = re.search(r"[?&]file=([^&]+)", url)
    name = match.group(1) if match else url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    if "." not in name:
        title = link.get("title") or ""
        found = re.search(r"[^\s:]+(?:\s[^\s:]+)*\.(?:pptx?|pdf)\b", title, re.I)
        name = found.group(0) if found else title
    return name.strip()


def is_design_review_deck(name: str) -> bool:
    """A file named DesignReview / Design Review / DR-<number>."""
    flat = re.sub(r"[\s_\-\[\]]+", "", name.lower())
    return "designreview" in flat or bool(re.match(r"dr[\s_\-\[]*020\d{4}", name.strip().lower()))


def deck_numbers(name: str) -> set[str]:
    return set(PROJECT_NO_RE.findall(name or ""))


def deck_revision(name: str) -> tuple | None:
    """'..._RevB.4.0.pptx' -> ('B', (4, 0)); None when the name carries no revision."""
    matches = REVISION_RE.findall(name or "")
    if not matches:
        return None
    letter, digits = matches[-1]
    return letter.upper(), tuple(int(part) for part in re.findall(r"\d+", digits))


def is_newer_revision(new: str, old: str) -> bool:
    """True only when both names carry a revision and new's is later (RevA < RevB, RevB.3.0 < RevB.4.0)."""
    new_rev, old_rev = deck_revision(new), deck_revision(old)
    return bool(new_rev and old_rev and new_rev > old_rev)


def is_support_email(subject: str) -> bool:
    return bool(SUPPORT_SUBJECT_RE.search(subject or ""))


def design_reviews(emails: list, own: set[str], kickoff_previous: str = "") -> tuple[str, list[str]]:
    """(current DR, previous DRs) from a project's emails, oldest first.

    Current: a design review deck named with one of the project's 020 numbers (own, or a number in that email's
    subject), or a slides link in a design review email when the deck carries no number. A later deck replaces it
    only when its revision is verifiably newer. Previous: the kickoff's "Previous DR" link and decks named for
    another 020 number (or unnumbered decks outside DR emails). "Applications Project Support" emails are skipped."""
    current, current_name = "", ""
    previous: list[tuple[str, str]] = []
    kickoff_name = link_file_name({"url": kickoff_previous}).lower() if kickoff_previous else ""

    def consider(url: str, name: str, is_current: bool) -> None:
        nonlocal current, current_name
        if is_current:
            if not current:
                current, current_name = url, name
            elif name.lower() != current_name.lower() and is_newer_revision(name, current_name):
                current, current_name = url, name
            return
        key = (name or url).lower()
        if all(key != (item_name or item_url).lower() and url != item_url for item_url, item_name in previous):
            previous.append((url, name))

    if kickoff_previous:
        consider(kickoff_previous, kickoff_name, bool(deck_numbers(kickoff_name) & own))
    for email in sorted(emails, key=lambda item: item.date):
        if is_support_email(email.subject):
            continue
        mine = own | deck_numbers(email.subject)
        dr_email = bool(DR_SUBJECT_RE.search(email.subject or ""))
        for link in extract_links(email.body_text):
            url = link["url"]
            name = link_file_name(link)
            deck = is_design_review_deck(name)
            if not deck and not (dr_email and is_slides_url(url)):
                continue
            numbers = deck_numbers(name)
            if kickoff_previous and (url == kickoff_previous or (kickoff_name and name.lower() == kickoff_name)):
                consider(url, name, bool(numbers & own))
            elif numbers:
                consider(url, name, bool(numbers & mine))
            else:
                consider(url, name, dr_email or bool(DR_LABEL_RE.fullmatch(name)))
    out = [url for url, name in previous if url != current and (name or url).lower() != current_name.lower()]
    return current, out


def strip_project_prefix(name: str) -> str:
    stripped = SO_PREFIX_RE.sub("", name or "").strip()
    return re.sub(r"\s+", " ", stripped.lstrip(" -").strip()) or (name or "").strip()
