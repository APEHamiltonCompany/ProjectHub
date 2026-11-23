from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


@dataclass
class Email:
    message_id: str
    subject: str
    sender: str
    to: str
    cc: str
    date: datetime
    body_text: str
    source_path: str = ""
    entry_id: str = ""
    end: datetime | None = None


@dataclass
class Task:
    id: str
    title: str
    stage: str = ""
    due: Optional[date] = None
    done: bool = False
    done_date: Optional[date] = None
    trigger_date: Optional[date] = None
    project_no: str = ""
    source: str = "tracker"
    mark: str = " "
    note: str = ""
    parent: str = ""
    milestone: bool = False
    mandatory: bool = False
    due_source: str = ""

    @property
    def status(self) -> str:
        if self.mark == "-":
            return "not needed"
        return "done" if self.done else "open"


@dataclass
class Project:
    folder: str
    name: str
    note_path: Optional[str] = None
    customer: str = ""
    project_no: str = ""
    so: list[str] = field(default_factory=list)
    qte: list[str] = field(default_factory=list)
    level: str = ""
    stage: str = "Unknown"
    stage_manual: str = ""
    stage_manual_source: str = ""
    id_prefix: str = ""
    so_dismissed: list[str] = field(default_factory=list)
    assigned: str = ""
    tasks: list[Task] = field(default_factory=list)
    triggers: dict[str, date] = field(default_factory=dict)
    schedule_source: str = ""
    schedule_mine: bool | None = None
    schedule_project: str = ""
    schedule_unlinked: bool = False

    @property
    def key(self) -> str:
        return self.project_no or (self.so[0] if self.so else "") or (self.qte[0] if self.qte else "") or self.name
