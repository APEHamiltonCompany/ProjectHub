from __future__ import annotations

import html
from datetime import date
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

import plotly.graph_objects as go

from .models import Project, Task

COLORS = {
    "overdue": "#c0392b",
    "due-soon": "#e67e22",
    "upcoming": "#2c6fbb",
    "done": "#9aa0a6",
    "not-needed": "#c4c9cf",
    "open": "#2c6fbb",
}

LABELS = {
    "overdue": "Overdue",
    "due-soon": "Due soon",
    "upcoming": "Upcoming",
    "done": "Done",
    "not-needed": "Not needed",
    "open": "No date",
}

CSS = """
body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#1f2328;background:#fafbfc}
h1{font-size:22px;margin:0 0 4px 0}
h2{font-size:16px;margin:28px 0 8px 0;border-bottom:1px solid #d8dee4;padding-bottom:4px}
.meta{color:#57606a;font-size:12px;margin-bottom:16px}
.kpis{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}
.kpi{background:#fff;border:1px solid #d8dee4;border-radius:6px;padding:10px 16px;min-width:120px}
.kpi .n{font-size:24px;font-weight:600;display:block}
.kpi .l{font-size:12px;color:#57606a;text-transform:uppercase;letter-spacing:.04em}
.kpi.overdue .n{color:#c0392b}
.kpi.soon .n{color:#e67e22}
table{border-collapse:collapse;width:100%;background:#fff;font-size:13px}
th,td{border:1px solid #d8dee4;padding:6px 8px;text-align:left;vertical-align:top}
th{background:#f0f3f6;font-weight:600}
tr:nth-child(even) td{background:#fbfcfd}
.st-overdue{color:#c0392b;font-weight:600}
.st-due-soon{color:#e67e22;font-weight:600}
.st-upcoming{color:#2c6fbb}
.st-done{color:#9aa0a6}
.st-not-needed{color:#c4c9cf;text-decoration:line-through}
.st-open{color:#57606a}
.empty{color:#57606a;font-style:italic}
a{color:#2c6fbb;text-decoration:none}
a:hover{text-decoration:underline}
"""


def status_for(task: Task, today: date, due_soon_days: int) -> str:
    if task.mark == "-":
        return "not-needed"
    if task.done:
        return "done"
    if task.due is None:
        return "open"
    if task.due < today:
        return "overdue"
    if (task.due - today).days <= due_soon_days:
        return "due-soon"
    return "upcoming"


def obsidian_link(path: str, text: str) -> str:
    return '<a href="obsidian://open?path=%s">%s</a>' % (quote(path, safe=""), html.escape(text))


def folder_link(path: str) -> str:
    url = "file:///" + quote(path.replace("\\", "/"), safe="/:")
    return '<a href="%s">folder</a>' % html.escape(url, quote=True)


def build_figure(projects: Iterable[Project], today: date, due_soon_days: int) -> go.Figure:
    fig = go.Figure()
    legend_seen: set[str] = set()
    rows = []
    for project in projects:
        dated = [t for t in project.tasks if t.due is not None]
        if not dated:
            continue
        rows.append((min(t.due for t in dated), project, dated))
    rows.sort(key=lambda item: (item[0], item[1].name.lower()))
    for _earliest, project, dated in rows:
        for task in sorted(dated, key=lambda t: t.due):
            status = status_for(task, today, due_soon_days)
            color = COLORS[status]
            start = task.trigger_date or task.due
            if start > task.due:
                start = task.due
            fig.add_trace(
                go.Scatter(
                    x=[start.isoformat(), task.due.isoformat()],
                    y=[project.name, project.name],
                    mode="lines+markers",
                    line=dict(color=color, width=10),
                    marker=dict(color=color, size=7),
                    name=LABELS[status],
                    legendgroup=status,
                    showlegend=status not in legend_seen,
                    hovertemplate=(
                        "<b>%s</b><br>%s<br>due %s<br>%s<extra></extra>"
                        % (
                            html.escape(project.name),
                            html.escape(task.title),
                            task.due.isoformat(),
                            LABELS[status],
                        )
                    ),
                )
            )
            legend_seen.add(status)
    fig.add_vline(x=today.isoformat(), line_dash="dash", line_color="#333333")
    fig.update_layout(
        barmode="overlay",
        height=max(260, 60 + 34 * len(rows)),
        margin=dict(l=10, r=20, t=30, b=40),
        plot_bgcolor="#ffffff",
        paper_bgcolor="#ffffff",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        xaxis=dict(type="date", title="", showgrid=True, gridcolor="#eaeef2"),
        yaxis=dict(autorange="reversed", tickfont=dict(size=11)),
    )
    return fig


def _task_rows(projects: Iterable[Project], today: date, due_soon_days: int) -> list[tuple]:
    rows = []
    for project in projects:
        for task in project.tasks:
            if task.due is None:
                continue
            rows.append((task.due, project, task, status_for(task, today, due_soon_days)))
    rows.sort(key=lambda r: (r[0], r[1].name.lower(), r[2].title.lower()))
    return rows


def open_steps(project: Project) -> int:
    return sum(1 for task in project.tasks if task.due is None and not task.done and task.mark != "-")


def _ident(project: Project) -> str:
    values = project.so + project.qte
    return ", ".join(values) if values else ""


def render(
    projects: list[Project],
    unmatched: list[dict],
    today: date,
    due_soon_days: int,
    generated: str,
    plotly_js: str = "cdn",
) -> str:
    rows = _task_rows(projects, today, due_soon_days)
    overdue = [r for r in rows if r[3] == "overdue"]
    soon = [r for r in rows if r[3] == "due-soon"]
    open_tasks = [r for r in rows if not r[2].done]

    fig = build_figure(projects, today, due_soon_days)
    include = "cdn" if plotly_js == "cdn" else True
    chart = fig.to_html(full_html=False, include_plotlyjs=include, config={"displaylogo": False})

    parts = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="en"><head><meta charset="utf-8">')
    parts.append("<title>Project Deadline Tracker</title>")
    parts.append("<style>%s</style>" % CSS)
    parts.append("</head><body>")
    parts.append("<h1>Project Deadline Tracker</h1>")
    parts.append('<div class="meta">Generated %s &middot; %d projects &middot; due-soon window %d days</div>'
                 % (html.escape(generated), len(projects), due_soon_days))
    parts.append('<div class="kpis">')
    for css, count, label in (
        ("overdue", len(overdue), "Overdue"),
        ("soon", len(soon), "Due soon"),
        ("", len(open_tasks), "Open tasks"),
        ("", len(projects), "Projects"),
    ):
        parts.append('<div class="kpi %s"><span class="n">%d</span><span class="l">%s</span></div>'
                     % (css, count, html.escape(label)))
    parts.append("</div>")

    parts.append("<h2>Timeline</h2>")
    parts.append(chart)

    parts.append("<h2>Tasks by due date</h2>")
    if rows:
        parts.append("<table><thead><tr>")
        for head in ("Project", "Customer", "Project #", "SO/QTE", "Stage", "Task", "Due", "Days left", "Status"):
            parts.append("<th>%s</th>" % html.escape(head))
        parts.append("</tr></thead><tbody>")
        for due, project, task, status in rows:
            days = "" if due is None else str((due - today).days)
            parts.append("<tr>")
            parts.append("<td>%s</td>" % obsidian_link(project.note_path or project.folder, project.name))
            parts.append("<td>%s</td>" % html.escape(project.customer or ""))
            parts.append("<td>%s</td>" % html.escape(project.project_no or ""))
            parts.append("<td>%s</td>" % html.escape(_ident(project)))
            parts.append("<td>%s</td>" % html.escape(project.stage or "Unknown"))
            parts.append("<td>%s</td>" % html.escape(task.title))
            parts.append("<td>%s</td>" % (due.isoformat() if due else ""))
            parts.append("<td>%s</td>" % days)
            parts.append('<td class="st-%s">%s</td>' % (status, html.escape(LABELS[status])))
            parts.append("</tr>")
        parts.append("</tbody></table>")
    else:
        parts.append('<p class="empty">No dated tasks.</p>')

    parts.append("<h2>Projects</h2>")
    if projects:
        parts.append(
            "<table><thead><tr><th>Project</th><th>Customer</th><th>Project #</th><th>SO/QTE</th>"
            "<th>Stage</th><th>Open steps</th><th>Folder</th></tr></thead><tbody>"
        )
        for project in sorted(projects, key=lambda p: p.name.lower()):
            parts.append("<tr>")
            parts.append("<td>%s</td>" % obsidian_link(project.note_path or project.folder, project.name))
            parts.append("<td>%s</td>" % html.escape(project.customer or ""))
            parts.append("<td>%s</td>" % html.escape(project.project_no or ""))
            parts.append("<td>%s</td>" % html.escape(_ident(project)))
            parts.append("<td>%s</td>" % html.escape(project.stage or "Unknown"))
            parts.append("<td>open steps: %d</td>" % open_steps(project))
            parts.append("<td>%s</td>" % folder_link(project.folder))
            parts.append("</tr>")
        parts.append("</tbody></table>")
    else:
        parts.append('<p class="empty">No projects.</p>')

    parts.append("<h2>Unmatched emails</h2>")
    if unmatched:
        parts.append("<table><thead><tr><th>Date</th><th>Subject</th><th>Reason</th></tr></thead><tbody>")
        for entry in unmatched:
            parts.append("<tr><td>%s</td><td>%s</td><td>%s</td></tr>"
                         % (html.escape(entry["date"]), html.escape(entry["subject"]), html.escape(entry["reason"])))
        parts.append("</tbody></table>")
    else:
        parts.append('<p class="empty">No unmatched emails.</p>')

    parts.append("</body></html>")
    return "\n".join(parts)


def write(
    path: str | Path,
    projects: list[Project],
    unmatched: list[dict],
    today: date,
    due_soon_days: int,
    generated: str,
    plotly_js: str = "cdn",
) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        render(projects, unmatched, today, due_soon_days, generated, plotly_js),
        encoding="utf-8",
    )
