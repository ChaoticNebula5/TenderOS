"""Phase 4 plan item 30, as changed by the one-time spec edit: reading the
live-progress timeline (commercial approval to the open tab's refetch)
from compose logs.

The lines below are copied from two fake-stack runs on 2026-11-03
(`docker compose logs --no-color --timestamps api worker`): a passing run
of the edited spec, and a run with the web client made to ignore stream
messages, which failed at the live-tab wait. Worker lines are cut after the
part the script reads.
"""

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "e2e_step11_timeline", REPO_ROOT / "scripts" / "e2e_step11_timeline.py"
)
assert _spec and _spec.loader
timeline_mod = importlib.util.module_from_spec(_spec)
sys.modules["e2e_step11_timeline"] = timeline_mod
_spec.loader.exec_module(timeline_mod)

# Since plan item 44 the workflow jobs log from workflow_worker and the
# event consumers from maintenance_worker; the lines are otherwise as copied.
_W = "workflow_worker-1     | {ts} 2026-11-03 00:00:00,000 INFO rq.worker: "
_M = "maintenance_worker-1  | {ts} 2026-11-03 00:00:00,000 INFO rq.worker: "
_A = 'api-1     | {ts} INFO:     172.18.0.1:43930 - "{req} HTTP/1.1" {code}'
_RUN = "759d671a-6fb2-4328-8b85-49d53ce88eda"


def _api(ts: str, req: str, code: str = "200 OK") -> str:
    return _A.format(ts=ts, req=req, code=code)


def _job_start(ts: str) -> str:
    return _W.format(ts=ts) + (
        f"Worker 4ccf: workflow: tenderos_worker.jobs.workflow.resume_workflow_job('{_RUN}', 'af29')"
    )


def _job_end(ts: str) -> str:
    return _W.format(ts=ts) + (
        f"Successfully completed tenderos_worker.jobs.workflow.resume_workflow_job('{_RUN}', 'af29')"
    )


def _event(ts: str, kind: str = "WorkflowRunStatusChanged") -> str:
    return _M.format(ts=ts) + (
        "Successfully completed tenderos_worker.jobs.placeholder.process_outbox_event("
        f"'0b7c', 'workflow_run', '{kind}') job in 0:00:00.001s"
    )


_TASKS = f"GET /workflow-runs/{_RUN}/tasks"
_STREAM = f"GET /workflow-runs/{_RUN}/events/stream?ticket=[redacted]"

# The passing run (final run 1): the resource gate's decision, the live
# tab's first /tasks and two stream opens, the commercial decision, the
# resume job and its events, the pushed refetch, then the proposal.
PASSING = [
    _api("2026-11-03T08:13:02.127200035Z", "POST /gates/b481/decision"),
    _event("2026-11-03T08:13:03.132662225Z"),
    _api("2026-11-03T08:13:12.138715428Z", _TASKS),
    _api("2026-11-03T08:13:12.144557969Z", _STREAM),
    _api("2026-11-03T08:13:12.147111009Z", _STREAM),
    _api("2026-11-03T08:13:12.215788859Z", "POST /gates/af29/decision"),
    _event("2026-11-03T08:13:13.127613882Z"),
    _job_start("2026-11-03T08:13:13.135123021Z"),
    _job_end("2026-11-03T08:13:20.014807375Z"),
    _event("2026-11-03T08:13:20.070832490Z"),
    _event("2026-11-03T08:13:20.092039605Z"),
    _api("2026-11-03T08:13:20.527716543Z", _TASKS),
    _api("2026-11-03T08:13:21.037647159Z", "POST /tenders/7038/proposal/generate", "202 Accepted"),
]


def test_the_passing_run():
    t = timeline_mod.read_timeline("\n".join(PASSING))

    assert t.approval == "2026-11-03T08:13:12.215Z"
    assert t.refetch == "2026-11-03T08:13:20.527Z"
    assert abs(t.approval_to_refetch_seconds - 8.311928) < 1e-6
    assert t.stream_opens_before_approval == 2
    assert t.stream_opens_between == 0
    assert t.job == "resume_workflow_job"
    assert t.job_started == "2026-11-03T08:13:13.135Z"
    assert t.job_ended == "2026-11-03T08:13:20.014Z"
    assert t.event_before_refetch == "WorkflowRunStatusChanged"
    assert abs(t.event_before_refetch_seconds - 0.435677) < 1e-6
    assert t.note == "refetch after a workflow event, no reconnect"


def test_the_tabs_own_first_fetch_is_not_the_refetch():
    """The /tasks answered before the approval is the tab settling."""
    t = timeline_mod.read_timeline("\n".join(PASSING))
    assert t.refetch != "2026-11-03T08:13:12.138Z"


def test_the_run_with_pushes_ignored_has_no_refetch():
    """Mutation run 3: the run stopped at the live-tab wait, so there is no
    proposal generation; the last gate decision is taken as the approval."""
    log = "\n".join(
        [
            _api("2026-11-03T08:02:43.082157384Z", "POST /gates/3169/decision"),
            _api("2026-11-03T08:02:53.315204592Z", _TASKS),
            _api("2026-11-03T08:02:53.319788932Z", _STREAM),
            _api("2026-11-03T08:02:53.323549206Z", _STREAM),
            _api("2026-11-03T08:02:53.412404636Z", "POST /gates/3db4/decision"),
            _job_start("2026-11-03T08:02:54.522276724Z"),
            _job_end("2026-11-03T08:03:01.600292604Z"),
            _event("2026-11-03T08:03:01.656859555Z"),
        ]
    )

    t = timeline_mod.read_timeline(log)

    assert t.approval == "2026-11-03T08:02:53.412Z"
    assert t.refetch is None and t.approval_to_refetch_seconds is None
    assert t.stream_opens_before_approval == 2
    assert t.job_ended == "2026-11-03T08:03:01.600Z"
    assert "last gate decision taken as the approval" in t.note
    assert "no /tasks request after the approval" in t.note


def test_a_stream_reopened_before_the_refetch_is_flagged():
    log = PASSING[:9] + [_api("2026-11-03T08:13:15.000000000Z", _STREAM)] + PASSING[9:]
    t = timeline_mod.read_timeline("\n".join(log))
    assert t.stream_opens_between == 1
    assert "may not be a push" in t.note


def test_stream_opens_before_an_earlier_gate_are_not_counted():
    log = [_api("2026-11-03T08:12:50.000000000Z", _STREAM)] + PASSING
    t = timeline_mod.read_timeline("\n".join(log))
    assert t.stream_opens_before_approval == 2


def test_no_gate_decision():
    t = timeline_mod.read_timeline(_job_start("2026-11-03T08:13:13.135123021Z"))
    assert t.approval is None
    assert t.note == "no gate decision in the log, so no commercial approval to read"


def test_markdown_has_every_row():
    text = timeline_mod.to_markdown(timeline_mod.read_timeline("\n".join(PASSING)))
    assert "| Approval to refetch | 8.312 s |" in text
    assert "| Stream opens between approval and refetch | 0 |" in text
    assert "WorkflowRunStatusChanged, 0.436 s before it" in text
