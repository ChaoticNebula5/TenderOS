"""Read one E2E run's live-progress timeline from the stack's logs (Phase 4
plan item 30; the check moved by the one-time spec edit).

Until the spec edit, step 11 opened a second tab and waited for a /tasks
refetch after the red-team click, but the manual red team writes no
workflow event, and the refetch that met the wait was the tab's own first
fetch in every CI run read (KNOWN_LIMITATIONS, "E2E step 11 depends on the
workflow still running"). The check now runs at step 8: a second tab on the
Upload/Processing screen, settled (its first /tasks answered, both event
streams requested), then the commercial-gate approval, which resumes the
workflow, and the tab's next /tasks request, which only a pushed event can
cause. This script reports that sequence from the server's side.

Input: `docker compose logs --no-color --timestamps` for api and worker.
- The approval: the api's access line for the last `POST /gates/<id>/decision`
  before the first `POST /tenders/<id>/proposal/generate` (the commercial
  gate is the last gate the spec approves before generating the proposal).
  A run that failed at the live-tab check never generates a proposal; then
  the last gate decision in the log is taken, and the note says so.
- The open tab's refetch: the first `GET /workflow-runs/<id>/tasks` after
  the approval. uvicorn logs a plain response when it is sent, so this is
  the refetch's answer, not its start.
- Stream opens: `GET /workflow-runs/<id>/events/stream` lines (logged when
  the streaming response starts). Before the approval they show the tab
  settled; between the approval and the refetch they would be a reconnect,
  whose own refetch is not a push.
- The resume job: the first workflow job the worker starts after the
  approval ("workflow: tenderos_worker.jobs.workflow.<job>(" to
  "Successfully completed ...<job>(").
- Workflow events: the worker's "Successfully completed tenderos_worker.
  jobs.placeholder.process_outbox_event('<id>', 'workflow_...', '<Type>')"
  lines. The api's SSE pushes are not logged (it polls the outbox about
  once a second), so the last such line before the refetch is a proxy for
  the event pushed to the tab, not the push itself.

Output: a Markdown table (for the job summary) and, with --json, a JSON
file. This records, it does not judge: the exit code is 0 whenever the log
could be read, 2 when it could not.

Run: uv run python scripts/e2e_step11_timeline.py <compose.log> [--json out.json]
"""

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

_LINE = re.compile(
    r"^(?P<service>[\w-]+?)-\d+\s+\|\s+(?P<ts>\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z)\s(?P<msg>.*)$"
)
_DECISION = re.compile(r'"POST /gates/[^/ ]+/decision HTTP')
_PROPOSAL = re.compile(r'"POST /tenders/[^/ ]+/proposal/generate HTTP')
_TASKS = re.compile(r'"GET /workflow-runs/[^/ ]+/tasks HTTP')
_STREAM = re.compile(r'"GET /workflow-runs/[^/ ]+/events/stream')
_JOB_START = re.compile(r"workflow: tenderos_worker\.jobs\.workflow\.(?P<job>\w+)\(")
_JOB_END = re.compile(r"Successfully completed tenderos_worker\.jobs\.workflow\.(?P<job>\w+)\(")
_WORKFLOW_EVENT = re.compile(
    r"Successfully completed tenderos_worker\.jobs\.placeholder\.process_outbox_event\("
    r"'[^']*', 'workflow_\w+', '(?P<type>\w+)'\)"
)


@dataclass
class Timeline:
    approval: str | None
    refetch: str | None
    approval_to_refetch_seconds: float | None
    stream_opens_before_approval: int
    stream_opens_between: int
    job: str | None
    job_started: str | None
    job_ended: str | None
    event_before_refetch: str | None
    event_before_refetch_seconds: float | None
    note: str


def _parse_ts(ts: str) -> datetime:
    """Docker writes nanoseconds; datetime takes at most microseconds."""
    whole, _, frac = ts.rstrip("Z").partition(".")
    return datetime.fromisoformat(f"{whole}.{(frac + '000000')[:6]}+00:00")


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z") if moment else None


def read_timeline(log_text: str) -> Timeline:
    decisions: list[datetime] = []
    proposal: datetime | None = None
    tasks: list[datetime] = []
    streams: list[datetime] = []
    job_starts: list[tuple[datetime, str]] = []
    job_ends: list[tuple[datetime, str]] = []
    events: list[tuple[datetime, str]] = []

    for raw in log_text.splitlines():
        match = _LINE.match(raw)
        if not match:
            continue
        service, moment, msg = match["service"], _parse_ts(match["ts"]), match["msg"]
        if service == "api":
            if _DECISION.search(msg):
                decisions.append(moment)
            elif proposal is None and _PROPOSAL.search(msg):
                proposal = moment
            elif _TASKS.search(msg):
                tasks.append(moment)
            elif _STREAM.search(msg):
                streams.append(moment)
        elif service == "worker":
            if event := _WORKFLOW_EVENT.search(msg):
                events.append((moment, event["type"]))
            elif start := _JOB_START.search(msg):
                job_starts.append((moment, start["job"]))
            elif end := _JOB_END.search(msg):
                job_ends.append((moment, end["job"]))

    empty = Timeline(None, None, None, 0, 0, None, None, None, None, None, "")
    before_proposal = [moment for moment in decisions if proposal is None or moment < proposal]
    if not before_proposal:
        empty.note = "no gate decision in the log, so no commercial approval to read"
        return empty
    approval = before_proposal[-1]
    # The tab settles shortly before the approval; a stream opened before an
    # earlier gate's approval belongs to some other screen.
    window_start = (
        before_proposal[-2]
        if len(before_proposal) > 1
        else datetime.min.replace(tzinfo=approval.tzinfo)
    )
    opens_before = sum(1 for moment in streams if window_start < moment <= approval)

    after = [moment for moment in tasks if moment > approval]
    refetch = after[0] if after else None
    between = (
        sum(1 for moment in streams if approval < moment < refetch) if refetch is not None else 0
    )

    job = started = ended = None
    later_starts = [(moment, name) for moment, name in job_starts if moment > approval]
    if later_starts:
        started, job = later_starts[0]
        ends = [moment for moment, name in job_ends if name == job and moment >= started]
        ended = ends[0] if ends else None

    event_type: str | None = None
    event_gap: float | None = None
    if refetch is not None:
        prior = [(moment, kind) for moment, kind in events if approval < moment <= refetch]
        if prior:
            event_type = prior[-1][1]
            event_gap = (refetch - prior[-1][0]).total_seconds()

    if refetch is None:
        note = "no /tasks request after the approval: no push reached the open tab"
    elif between:
        note = "a stream reopened between the approval and the refetch: it may not be a push"
    elif event_type is None:
        note = "no workflow event logged between the approval and the refetch"
    else:
        note = "refetch after a workflow event, no reconnect"
    if proposal is None:
        note = (
            "no proposal generation in the log: the last gate decision taken as the approval; "
            + note
        )
    return Timeline(
        _iso(approval),
        _iso(refetch),
        (refetch - approval).total_seconds() if refetch is not None else None,
        opens_before,
        between,
        job,
        _iso(started),
        _iso(ended),
        event_type,
        event_gap,
        note,
    )


def _seconds(value: float | None, suffix: str = "") -> str:
    return "n/a" if value is None else f"{value:.3f} s{suffix}"


def to_markdown(timeline: Timeline) -> str:
    rows = [
        ("Commercial approval (api)", timeline.approval or "n/a"),
        ("Open tab's next /tasks (api, answered)", timeline.refetch or "n/a"),
        ("Approval to refetch", _seconds(timeline.approval_to_refetch_seconds)),
        ("Stream opens before the approval", str(timeline.stream_opens_before_approval)),
        ("Stream opens between approval and refetch", str(timeline.stream_opens_between)),
        ("Workflow job after the approval", timeline.job or "n/a"),
        ("Job started", timeline.job_started or "n/a"),
        ("Job ended", timeline.job_ended or "n/a"),
        (
            "Last workflow event before the refetch (worker log, a proxy for the push)",
            "n/a"
            if timeline.event_before_refetch is None
            else f"{timeline.event_before_refetch}, "
            f"{_seconds(timeline.event_before_refetch_seconds, ' before it')}",
        ),
        ("Note", timeline.note),
    ]
    lines = ["### E2E live-progress timeline (commercial approval to the open tab)", ""]
    lines += ["| | |", "|---|---|"]
    lines += [f"| {label} | {value} |" for label, value in rows]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("log", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    try:
        text = args.log.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"could not read {args.log}: {exc}", file=sys.stderr)
        return 2
    timeline = read_timeline(text)
    print(to_markdown(timeline))
    if args.json:
        args.json.write_text(json.dumps(asdict(timeline), indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
