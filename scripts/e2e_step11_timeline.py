"""Read one E2E run's step 11 timeline from the stack's logs (Phase 4 plan
item 30).

Step 11 of the golden-path spec passes only if the workflow's resume job is
still running when the red team is clicked: the second tab waits for a
workflow event, and the manual red team writes none (KNOWN_LIMITATIONS,
"E2E step 11 depends on the workflow still running when the red team is
clicked"). Locally the click came 5.1 and 5.3 s before that job ended. On a
CI runner both sides of the race move, so CI records the margin on every
run before the job is made required.

Input: `docker compose logs --no-color --timestamps` for api and worker.
- The click: the api's access line `POST /tenders/<id>/redteam/run`.
- Workflow jobs: the worker's RQ lines, "workflow: tenderos_worker.jobs.
  workflow.<job>(" when one starts and "Successfully completed tenderos_
  worker.jobs.workflow.<job>(" when it ends. One worker process runs one
  job at a time, so each start pairs with the next end.
- Task fetches: the api's `GET /workflow-runs/<id>/tasks` lines. Step 11
  waits for one from the second tab (`full-flow.spec.ts:1024`), registered
  after that tab's `networkidle` and before the click; Playwright's
  waitForRequest matches only requests issued after it is registered.
  Both sides of the click are reported. On the batch 2b run (2026-10-23,
  passed) the log shows one task fetch 0.855 s before the click and none
  after: the red team caused none, so the wait was met by that earlier
  fetch, issued after the registration. Two candidate causes: (a) the
  tab's own first data fetch, issued after `networkidle` had already
  resolved; (b) a refetch driven by a workflow event pushed to the tab
  while the resume job ran. In that run the fetch reached the api before
  either of the tab's event streams had opened (55.583, streams 55.584 and
  55.592), which weighs against (b); server logs cannot show when the wait
  was registered, so neither is proven.
- Workflow events: the worker's "Successfully completed tenderos_worker.
  jobs.placeholder.process_outbox_event('<id>', 'workflow_...', '<Type>')"
  lines, the dispatcher handing each outbox event on. The api's own SSE
  pushes are not logged (it polls the outbox about once a second), so the
  last such line before the fetch is a proxy for the last event the tab
  could have been pushed, not the push itself.

Output: a Markdown table (for the job summary) and, with --json, a JSON
file. The margin is the job's end minus the click: positive means the job
was still running at the click, by that many seconds; negative means it
had already ended. This records, it does not judge: the exit code is 0
whenever the log could be read, 2 when it could not.

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
_CLICK = re.compile(r'"POST /tenders/[^/ ]+/redteam/run HTTP')
_JOB_START = re.compile(r"workflow: tenderos_worker\.jobs\.workflow\.(?P<job>\w+)\(")
_JOB_END = re.compile(r"Successfully completed tenderos_worker\.jobs\.workflow\.(?P<job>\w+)\(")
_REFETCH = re.compile(r'"GET /workflow-runs/[^/ ]+/tasks HTTP')
_WORKFLOW_EVENT = re.compile(
    r"Successfully completed tenderos_worker\.jobs\.placeholder\.process_outbox_event\("
    r"'[^']*', 'workflow_\w+', '(?P<type>\w+)'\)"
)


@dataclass
class Timeline:
    click: str | None
    job: str | None
    job_started: str | None
    job_ended: str | None
    margin_seconds: float | None
    job_running_at_click: bool | None
    task_fetches_after_click: int
    last_task_fetch_before_click_seconds: float | None
    note: str
    # The last workflow outbox event the worker logged at or before that
    # last task fetch, and how long before the fetch (a proxy for the push).
    event_before_fetch: str | None = None
    event_before_fetch_seconds: float | None = None


def _parse_ts(ts: str) -> datetime:
    """Docker writes nanoseconds; datetime takes at most microseconds."""
    whole, _, frac = ts.rstrip("Z").partition(".")
    return datetime.fromisoformat(f"{whole}.{(frac + '000000')[:6]}+00:00")


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z") if moment else None


def read_timeline(log_text: str) -> Timeline:
    click: datetime | None = None
    jobs: list[tuple[str, datetime, datetime | None]] = []
    open_job: tuple[str, datetime] | None = None
    refetches: list[datetime] = []
    events: list[tuple[datetime, str]] = []

    for raw in log_text.splitlines():
        match = _LINE.match(raw)
        if not match:
            continue
        service, moment, msg = match["service"], _parse_ts(match["ts"]), match["msg"]
        if service == "api":
            if click is None and _CLICK.search(msg):
                click = moment
            elif _REFETCH.search(msg):
                refetches.append(moment)
        elif service == "worker":
            if event := _WORKFLOW_EVENT.search(msg):
                events.append((moment, event["type"]))
            elif start := _JOB_START.search(msg):
                if open_job is not None:
                    jobs.append((open_job[0], open_job[1], None))
                open_job = (start["job"], moment)
            elif (
                (end := _JOB_END.search(msg)) and open_job is not None and end["job"] == open_job[0]
            ):
                jobs.append((open_job[0], open_job[1], moment))
                open_job = None
    if open_job is not None:
        jobs.append((open_job[0], open_job[1], None))

    if click is None:
        return Timeline(None, None, None, None, None, None, 0, None, "no red-team click in the log")
    after = sum(1 for moment in refetches if moment > click)
    earlier = [moment for moment in refetches if moment <= click]
    last_before = (click - earlier[-1]).total_seconds() if earlier else None
    event_type: str | None = None
    event_gap: float | None = None
    if earlier:
        prior = [(moment, kind) for moment, kind in events if moment <= earlier[-1]]
        if prior:
            event_type = prior[-1][1]
            event_gap = (earlier[-1] - prior[-1][0]).total_seconds()

    running = [j for j in jobs if j[1] <= click and (j[2] is None or j[2] >= click)]
    if running:
        name, started, ended = running[-1]
        margin = (ended - click).total_seconds() if ended else None
        note = "job running at the click" + ("" if ended else "; its end is not in the log")
    else:
        finished = [j for j in jobs if j[2] is not None and j[2] < click]
        if not finished:
            return Timeline(
                _iso(click),
                None,
                None,
                None,
                None,
                False,
                after,
                last_before,
                "no workflow job in the log",
                event_type,
                event_gap,
            )
        name, started, ended = finished[-1]
        assert ended is not None
        margin = (ended - click).total_seconds()
        note = "no workflow job running at the click; the last one had ended"
    if after == 0:
        note += (
            "; no task fetch after the click: the red team caused none, so if the run"
            " passed the wait was met by an earlier fetch"
        )
    return Timeline(
        _iso(click),
        name,
        _iso(started),
        _iso(ended),
        margin,
        bool(running),
        after,
        last_before,
        note,
        event_type,
        event_gap,
    )


def to_markdown(timeline: Timeline) -> str:
    margin = "n/a" if timeline.margin_seconds is None else f"{timeline.margin_seconds:+.3f} s"
    rows = [
        ("Red-team click (api)", timeline.click or "n/a"),
        ("Workflow job at the click", timeline.job or "n/a"),
        ("Job started", timeline.job_started or "n/a"),
        ("Job ended", timeline.job_ended or "n/a"),
        ("Margin (job end minus click)", margin),
        (
            "Job running at the click",
            "n/a"
            if timeline.job_running_at_click is None
            else str(timeline.job_running_at_click).lower(),
        ),
        ("Task fetches after the click", str(timeline.task_fetches_after_click)),
        (
            "Last task fetch before the click",
            "n/a"
            if timeline.last_task_fetch_before_click_seconds is None
            else f"{timeline.last_task_fetch_before_click_seconds:.3f} s before",
        ),
        (
            "Last workflow event before that fetch (worker log, a proxy for the push)",
            "n/a"
            if timeline.event_before_fetch is None or timeline.event_before_fetch_seconds is None
            else f"{timeline.event_before_fetch}, {timeline.event_before_fetch_seconds:.3f} s before it",
        ),
        ("Note", timeline.note),
    ]
    lines = ["### E2E step 11 timeline", "", "| | |", "|---|---|"]
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
