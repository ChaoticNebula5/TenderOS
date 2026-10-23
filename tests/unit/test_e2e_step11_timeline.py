"""Phase 4 plan item 30: reading the step 11 timeline from compose logs.

The log lines below are copied in shape from a real fake-stack run
(2026-10-23, batch 2b: `docker compose logs --no-color --timestamps api
worker`), with ids shortened. That run's figures are the first case.
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

_WORKER = "worker-1  | {ts} 2026-10-23 00:00:00,000 INFO rq.worker: "
_API = 'api-1  | {ts} INFO:     172.22.0.1:43396 - "{req} HTTP/1.1" {code}'


def _start(ts: str, job: str = "resume_workflow_job") -> str:
    return _WORKER.format(ts=ts) + (
        f"Worker 5353: workflow: tenderos_worker.jobs.workflow.{job}('b018', '4480') (resume_b018_4480)"
    )


def _end(ts: str, job: str = "resume_workflow_job") -> str:
    return _WORKER.format(ts=ts) + (
        f"Successfully completed tenderos_worker.jobs.workflow.{job}('b018', '4480') "
        "job in 0:00:07.352589s on worker 5353"
    )


def _api(ts: str, req: str, code: str = "200 OK") -> str:
    return _API.format(ts=ts, req=req, code=code)


def _event(ts: str, aggregate: str = "workflow_run", kind: str = "WorkflowRunStatusChanged") -> str:
    return _WORKER.format(ts=ts) + (
        "Successfully completed tenderos_worker.jobs.placeholder.process_outbox_event("
        f"'dc78', '{aggregate}', '{kind}') job in 0:00:00.001190s on worker 5353"
    )


CLICK = _api("2026-10-23T10:57:56.438221604Z", "POST /tenders/49da/redteam/run", "202 Accepted")
FETCH_BEFORE = _api("2026-10-23T10:57:55.582953870Z", "GET /workflow-runs/b018/tasks")


def test_the_batch_2b_run():
    log = "\n".join(
        [
            _start("2026-10-23T10:57:44.177437621Z"),
            _end("2026-10-23T10:57:50.661642815Z"),
            _event("2026-10-23T10:57:54.217175172Z"),
            _start("2026-10-23T10:57:54.224982706Z"),
            FETCH_BEFORE,
            CLICK,
            _end("2026-10-23T10:58:01.588593016Z"),
        ]
    )

    t = timeline_mod.read_timeline(log)

    assert t.event_before_fetch == "WorkflowRunStatusChanged"
    assert abs(t.event_before_fetch_seconds - 1.365778) < 1e-6
    assert t.click == "2026-10-23T10:57:56.438Z"
    assert t.job == "resume_workflow_job"
    assert t.job_started == "2026-10-23T10:57:54.224Z"
    assert t.job_ended == "2026-10-23T10:58:01.588Z"
    assert t.job_running_at_click is True
    assert abs(t.margin_seconds - 5.150372) < 1e-6
    assert t.task_fetches_after_click == 0
    assert abs(t.last_task_fetch_before_click_seconds - 0.855268) < 1e-6
    assert "no task fetch after the click" in t.note


def test_a_job_that_ended_before_the_click_gives_a_negative_margin():
    """Stage 2's failing shape: the workflow's last job ended before the
    click, so the wait had nothing left to see."""
    log = "\n".join(
        [
            _start("2026-10-16T06:10:25.000000000Z"),
            _end("2026-10-16T06:10:32.409000000Z"),
            _api("2026-10-16T06:10:40.858000000Z", "POST /tenders/x/redteam/run", "202 Accepted"),
        ]
    )

    t = timeline_mod.read_timeline(log)

    assert t.job_running_at_click is False
    assert abs(t.margin_seconds - (-8.449)) < 1e-6
    assert "had ended" in t.note


def test_a_fetch_after_the_click_is_counted():
    log = "\n".join(
        [
            _start("2026-10-23T10:57:54.224982706Z"),
            CLICK,
            _api("2026-10-23T10:57:58.000000000Z", "GET /workflow-runs/b018/tasks"),
            _api("2026-10-23T10:57:58.100000000Z", "OPTIONS /workflow-runs/b018/tasks"),
            _end("2026-10-23T10:58:01.588593016Z"),
        ]
    )

    t = timeline_mod.read_timeline(log)

    assert t.task_fetches_after_click == 1
    assert t.last_task_fetch_before_click_seconds is None
    assert t.note == "job running at the click"


def test_only_workflow_events_up_to_the_fetch_count():
    """Events after the fetch, and outbox events of other aggregates, are
    not candidates for having caused it."""
    log = "\n".join(
        [
            _event("2026-10-23T10:57:53.000000000Z", "workflow_task", "WorkflowTaskCompleted"),
            _event("2026-10-23T10:57:55.000000000Z", "compliance_entry", "ComplianceChanged"),
            FETCH_BEFORE,
            _event("2026-10-23T10:57:55.900000000Z"),
            _start("2026-10-23T10:57:54.224982706Z"),
            CLICK,
        ]
    )

    t = timeline_mod.read_timeline(log)

    assert t.event_before_fetch == "WorkflowTaskCompleted"
    # 10:57:55.582953 (nanoseconds truncated to microseconds) minus 10:57:53.
    assert abs(t.event_before_fetch_seconds - 2.582953) < 1e-6


def test_no_fetch_before_the_click_reports_no_event():
    log = "\n".join([_event("2026-10-23T10:57:53.000000000Z"), CLICK])
    t = timeline_mod.read_timeline(log)
    assert t.event_before_fetch is None and t.event_before_fetch_seconds is None


def test_a_job_still_running_when_the_log_ends():
    log = "\n".join([_start("2026-10-23T10:57:54.224982706Z"), CLICK])

    t = timeline_mod.read_timeline(log)

    assert t.job_running_at_click is True
    assert t.margin_seconds is None
    assert "end is not in the log" in t.note


def test_no_click_when_the_run_failed_earlier():
    t = timeline_mod.read_timeline(_start("2026-10-23T10:57:54.224982706Z"))
    assert t.click is None and t.note == "no red-team click in the log"


def test_other_services_and_other_jobs_are_ignored():
    log = "\n".join(
        [
            # The operations worker's jobs are not workflow jobs.
            "operations_worker-1  | 2026-10-23T10:57:55.000000000Z 2026-10-23 INFO rq.worker: "
            "Worker 9: operations: tenderos_worker.jobs.operations.run_operation('x') (op_x)",
            _start("2026-10-23T10:57:54.224982706Z"),
            "worker-1  | 2026-10-23T10:57:55.000000000Z 2026-10-23 INFO rq.worker: Successfully "
            "completed tenderos_worker.jobs.placeholder.process_outbox_event('a') job in 0:00:00.001s",
            CLICK,
            _end("2026-10-23T10:58:01.588593016Z"),
        ]
    )

    t = timeline_mod.read_timeline(log)

    assert t.job_ended == "2026-10-23T10:58:01.588Z"


def test_markdown_has_every_row():
    log = "\n".join(
        [_start("2026-10-23T10:57:54.224982706Z"), CLICK, _end("2026-10-23T10:58:01.588593016Z")]
    )
    text = timeline_mod.to_markdown(timeline_mod.read_timeline(log))
    assert "| Margin (job end minus click) | +5.150 s |" in text
    assert "| Task fetches after the click | 0 |" in text
