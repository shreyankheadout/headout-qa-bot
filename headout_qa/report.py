from __future__ import annotations

import json
from collections import defaultdict
from html import escape
from pathlib import Path

from .orchestrator import RunResult, ScenarioRun


def _status_badge(status: str, passed: bool | None, escalated: bool = False) -> str:
    # Escalation is now graded (see grader.py's escalation_justified check), so it's
    # a tag alongside PASS/FAIL rather than a badge that replaces them — a bot that
    # gives up and hands off unprompted must still show up as FAIL, not disappear
    # into a neutral ESCALATED badge that used to hide it from the pass/fail read.
    suffix = ' <span class="badge esc">ESCALATED</span>' if escalated else ""
    if passed is True:
        return '<span class="badge pass">PASS</span>' + suffix
    if passed is False:
        return '<span class="badge fail">FAIL</span>' + suffix
    if escalated:
        return '<span class="badge esc">ESCALATED</span>'
    return f'<span class="badge warn">{escape(status.upper())}</span>'


def _transcript_html(scenario: ScenarioRun, transcript: list[dict]) -> str:
    rows = []
    for event in transcript:
        role = event.get("role", "")
        text = escape(event.get("text", "")).replace("\n", "<br>")
        cls = f"msg {role}"
        rows.append(f'<div class="{cls}"><span class="tag">{role}</span>{text}</div>')
    if not rows:
        rows.append("<div class='msg system'>no transcript</div>")
    return "\n".join(rows)


def _grade_summary(run: ScenarioRun) -> str:
    parts = []
    if run.escalated:
        parts.append("escalated to supervisor — AI handoff; no reply expected")
    if run.grade:
        allc = " ".join(("✓" if c.passed else "✗") + " " + escape(c.name) for c in run.grade.checks)
        parts.append(f"deterministic: {'PASS' if run.grade.passed else 'FAIL'} — {allc}")
        failed = [c for c in run.grade.checks if not c.passed]
        if failed:
            parts.append("failed: " + "; ".join(f"{escape(c.name)}: {escape(c.detail)}" for c in failed))
        if run.grade.notes:
            parts.append("notes: " + "; ".join(run.grade.notes))
    if run.llm_grade:
        parts.append(f"llm: {'PASS' if run.llm_grade.passed else 'FAIL'}")
        if run.llm_grade.notes:
            parts.append("llm notes: " + "; ".join(run.llm_grade.notes))
    if run.error:
        parts.append(f"error: {escape(run.error)}")
    if run.ticket_url:
        parts.append(f'ticket: <a href="{escape(run.ticket_url)}" target="_blank">{run.ticket_id}</a>')
    return " ".join(parts) if parts else ""


def _load_scenarios(run_dir: Path) -> list[ScenarioRun]:
    scenarios = []
    for path in sorted((run_dir / "scenarios").glob("*.json")):
        data = json.loads(path.read_text())
        grade = _load_grade(data.get("grade"))
        llm_grade = _load_grade(data.get("llm_grade"))
        scenarios.append(
            ScenarioRun(
                scenario_id=data["scenario_id"],
                node=data.get("node", "default"),
                variant=data.get("variant", "default"),
                status=data.get("status", "completed"),
                escalated=bool(data.get("escalated", False)),
                booking_id=data.get("booking_id"),
                l1=data.get("l1"),
                l2=data.get("l2"),
                l3=data.get("l3"),
                mood=data.get("mood"),
                conversation_id=data.get("conversation_id"),
                user_id=data.get("user_id"),
                ticket_id=data.get("ticket_id"),
                ticket_url=data.get("ticket_url"),
                grade=grade,
                llm_grade=llm_grade,
                error=data.get("error"),
                duration_seconds=float(data.get("duration_seconds", 0)),
                started_at=data.get("started_at", ""),
                ended_at=data.get("ended_at", ""),
            )
        )
        scenarios[-1]._transcript = data.get("transcript", [])  # type: ignore[attr-defined]
    return scenarios


def _load_grade(data) -> "Grade | None":
    from .grader import CheckResult, Grade

    if not data:
        return None
    return Grade(
        passed=data.get("passed", False),
        checks=[CheckResult(c["name"], c["passed"], c.get("detail", "")) for c in data.get("checks", [])],
        notes=data.get("notes", []),
    )


def build_report(result: RunResult | None, run_dir: Path) -> Path:
    run_dir = result.run_dir if result else run_dir
    scenarios = _load_scenarios(run_dir)
    by_node: dict[str, list[ScenarioRun]] = defaultdict(list)
    for s in scenarios:
        by_node[s.node].append(s)

    rows: list[str] = []
    for node in sorted(by_node):
        node_runs = by_node[node]
        total = len(node_runs)
        passed = sum(1 for r in node_runs if r.grade and r.grade.passed)
        failed = sum(1 for r in node_runs if r.grade and not r.grade.passed)
        escalated = sum(1 for r in node_runs if r.escalated)
        incomplete = total - passed - failed
        rows.append(
            f'<tr class="node-row"><td colspan="2"><strong>{escape(node)}</strong></td>'
            f'<td>{passed}</td><td>{failed}</td><td>{escalated}</td><td>{incomplete}</td></tr>'
        )
        for r in node_runs:
            passed_state = r.grade.passed if r.grade else None
            transcript = getattr(r, "_transcript", [])
            rows.append(
                f"<tr><td>{escape(r.scenario_id)}</td>"
                f"<td>{_status_badge(r.status, passed_state, r.escalated)}</td>"
                f"<td>{round(r.duration_seconds, 1)}s</td>"
                f"<td>{escape(r.variant)}</td>"
                f"<td>{_grade_summary(r)}</td>"
                f'<td><details><summary>transcript</summary><div class="chat">{_transcript_html(r, transcript)}</div></details></td></tr>'
            )

    total = len(scenarios)
    passed = sum(1 for r in scenarios if r.grade and r.grade.passed)
    failed = sum(1 for r in scenarios if r.grade and not r.grade.passed)
    escalated = sum(1 for r in scenarios if r.escalated)
    incomplete = total - passed - failed

    by_touchpoint: dict[tuple[str, str, str], list[ScenarioRun]] = defaultdict(list)
    for s in scenarios:
        by_touchpoint[(s.l1 or "(blank)", s.l2 or "(blank)", s.l3 or "(blank)")].append(s)
    touchpoint_rows: list[str] = []
    for key in sorted(by_touchpoint):
        tp_runs = by_touchpoint[key]
        tp_total = len(tp_runs)
        tp_passed = sum(1 for r in tp_runs if r.grade and r.grade.passed)
        tp_failed = sum(1 for r in tp_runs if r.grade and not r.grade.passed)
        tp_escalated = sum(1 for r in tp_runs if r.escalated)
        tp_incomplete = tp_total - tp_passed - tp_failed
        touchpoint_rows.append(
            f"<tr><td>{escape(' › '.join(key))}</td>"
            f"<td>{tp_total}</td><td>{tp_passed}</td><td>{tp_failed}</td>"
            f"<td>{tp_escalated}</td><td>{tp_incomplete}</td></tr>"
        )

    by_mood: dict[str, list[ScenarioRun]] = defaultdict(list)
    for s in scenarios:
        by_mood[s.mood or "(blank)"].append(s)
    mood_rows: list[str] = []
    for mood in sorted(by_mood):
        mood_runs = by_mood[mood]
        mood_total = len(mood_runs)
        mood_passed = sum(1 for r in mood_runs if r.grade and r.grade.passed)
        mood_failed = sum(1 for r in mood_runs if r.grade and not r.grade.passed)
        mood_escalated = sum(1 for r in mood_runs if r.escalated)
        mood_incomplete = mood_total - mood_passed - mood_failed
        mood_rows.append(
            f"<tr><td>{escape(mood)}</td>"
            f"<td>{mood_total}</td><td>{mood_passed}</td><td>{mood_failed}</td>"
            f"<td>{mood_escalated}</td><td>{mood_incomplete}</td></tr>"
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>QA Run Report</title>
<style>
body {{ font-family: -apple-system, system-ui, sans-serif; margin: 2rem; color: #1f2933; }}
h1 {{ font-size: 1.4rem; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 1rem; }}
th, td {{ border: 1px solid #e4e7eb; padding: 6px 10px; font-size: 0.85rem; vertical-align: top; }}
th {{ background: #f5f7fa; text-align: left; }}
.badge {{ padding: 2px 8px; border-radius: 10px; font-size: 0.75rem; font-weight: 600; }}
.pass {{ background: #e6f4ea; color: #137333; }}
.fail {{ background: #fce8e6; color: #c5221f; }}
.warn {{ background: #fef7e0; color: #b06000; }}
.esc {{ background: #e8f0fe; color: #1a56db; }}
.node-row td {{ background: #eef1f4; font-weight: 600; }}
.summary {{ display: flex; gap: 2rem; margin: 1rem 0; }}
.summary div {{ padding: 0.6rem 1.2rem; border-radius: 8px; background: #f5f7fa; }}
.chat {{ margin-top: 6px; font-family: ui-monospace, monospace; font-size: 0.8rem; }}
.msg {{ margin: 2px 0; }}
.msg .tag {{ display: inline-block; width: 60px; font-weight: 600; }}
.msg.user {{ color: #1a73e8; }}
.msg.bot {{ color: #188038; }}
.msg.system {{ color: #80868b; font-style: italic; }}
details summary {{ cursor: pointer; color: #1a73e8; }}
</style>
</head>
<body>
<h1>Headout AI Agent — QA Run Report</h1>
<div class="summary">
  <div>Total: <strong>{total}</strong></div>
  <div>Pass: <strong>{passed}</strong></div>
  <div>Fail: <strong>{failed}</strong></div>
  <div>Escalated: <strong>{escalated}</strong></div>
  <div>Incomplete: <strong>{incomplete}</strong></div>
</div>
<h2 style="font-size:1.1rem;margin-top:1.5rem;">By touchpoint (L1 › L2 › L3)</h2>
<table>
<thead>
<tr><th>Touchpoint</th><th>Total</th><th>Passed</th><th>Failed</th><th>Escalated</th><th>Incomplete</th></tr>
</thead>
<tbody>
{chr(10).join(touchpoint_rows) if touchpoint_rows else '<tr><td colspan="6">no scenarios</td></tr>'}
</tbody>
</table>
<h2 style="font-size:1.1rem;margin-top:1.5rem;">By guest mood</h2>
<table>
<thead>
<tr><th>Mood</th><th>Total</th><th>Passed</th><th>Failed</th><th>Escalated</th><th>Incomplete</th></tr>
</thead>
<tbody>
{chr(10).join(mood_rows) if mood_rows else '<tr><td colspan="6">no scenarios</td></tr>'}
</tbody>
</table>
<h2 style="font-size:1.1rem;margin-top:1.5rem;">By scenario</h2>
<table>
<thead>
<tr><th>Scenario</th><th>Status</th><th>Duration</th><th>Variant</th><th>Grade / notes</th><th>Transcript</th></tr>
</thead>
<tbody>
{chr(10).join(rows)}
</tbody>
</table>
</body>
</html>
"""
    out = run_dir / "report.html"
    out.write_text(html)
    return out
