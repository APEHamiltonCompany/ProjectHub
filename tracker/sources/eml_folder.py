from __future__ import annotations

import email
import email.policy
import hashlib
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Optional

from ..models import Email
from ..notes import lp

EMAIL_SUFFIXES = {".eml", ".msg"}


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("br", "p", "div", "tr", "li", "table"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _HTMLText()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html or "")
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _local_tz():
    try:
        from tzlocal import get_localzone

        return get_localzone()
    except Exception:
        return datetime.now().astimezone().tzinfo or timezone.utc


def _aware(value: Optional[datetime]) -> datetime:
    if value is None:
        return datetime.now().astimezone()
    if value.tzinfo is None:
        return value.replace(tzinfo=_local_tz())
    return value


def _fallback_id(subject: str, when: datetime) -> str:
    digest = hashlib.sha1((subject + "|" + when.isoformat()).encode("utf-8", "replace")).hexdigest()
    return "sha1-" + digest


def _body_from_message(msg) -> str:
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
    except Exception:
        part = None
    if part is None:
        if msg.is_multipart():
            for sub in msg.walk():
                if sub.get_content_type() == "text/plain":
                    part = sub
                    break
        else:
            part = msg
    if part is None:
        return ""
    try:
        content = part.get_content()
    except Exception:
        payload = part.get_payload(decode=True) or b""
        content = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    if not isinstance(content, str):
        content = str(content)
    if part.get_content_type() == "text/html":
        return html_to_text(content)
    return content


def parse_eml(path: str | Path) -> Optional[Email]:
    path = Path(path)
    try:
        raw = lp(path).read_bytes()
    except OSError:
        return None
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    subject = str(msg.get("Subject") or "").strip()
    sender = str(msg.get("From") or "").strip()
    to = str(msg.get("To") or "").strip()
    cc = str(msg.get("CC") or msg.get("Cc") or "").strip()
    try:
        when = parsedate_to_datetime(str(msg.get("Date") or ""))
    except (TypeError, ValueError):
        when = None
    when = _aware(when)
    message_id = str(msg.get("Message-ID") or "").strip().strip("<>")
    if not message_id:
        message_id = _fallback_id(subject, when)
    return Email(
        message_id=message_id,
        subject=subject,
        sender=sender,
        to=to,
        cc=cc,
        date=when,
        body_text=_body_from_message(msg),
        source_path=str(path),
    )


def parse_msg(path: str | Path) -> Optional[Email]:
    path = Path(path)
    try:
        import extract_msg
    except ImportError:
        return None
    try:
        msg = extract_msg.Message(str(lp(path)))
    except Exception:
        return None
    try:
        subject = (msg.subject or "").strip()
        sender = (msg.sender or "").strip()
        to = (msg.to or "").strip()
        cc = (msg.cc or "").strip()
        body = msg.body or ""
        when = _aware(getattr(msg, "date", None))
        message_id = str(getattr(msg, "messageId", "") or "").strip().strip("<>")
        if not message_id:
            message_id = _fallback_id(subject, when)
        return Email(
            message_id=message_id,
            subject=subject,
            sender=sender,
            to=to,
            cc=cc,
            date=when,
            body_text=body,
            source_path=str(path),
        )
    finally:
        try:
            msg.close()
        except Exception:
            pass


def parse_file(path: str | Path) -> Optional[Email]:
    suffix = Path(path).suffix.lower()
    if suffix == ".eml":
        return parse_eml(path)
    if suffix == ".msg":
        return parse_msg(path)
    return None


def iter_email_files(folders: Iterable[str | Path], recursive: bool = True) -> Iterable[Path]:
    for folder in folders or []:
        base = Path(folder)
        if not base.is_dir():
            continue
        if recursive:
            for path in sorted(base.rglob("*")):
                if path.suffix.lower() in EMAIL_SUFFIXES and lp(path).is_file():
                    yield path
        else:
            for path in sorted(base.iterdir()):
                if path.suffix.lower() in EMAIL_SUFFIXES and lp(path).is_file():
                    yield path


def collect(folders: Iterable[str | Path], since_days: int = 120, recursive: bool = True) -> list[Email]:
    cutoff = datetime.now().astimezone() - timedelta(days=since_days)
    emails = []
    for path in iter_email_files(folders, recursive=recursive):
        parsed = parse_file(path)
        if parsed is None:
            continue
        if parsed.date < cutoff:
            continue
        emails.append(parsed)
    return emails
