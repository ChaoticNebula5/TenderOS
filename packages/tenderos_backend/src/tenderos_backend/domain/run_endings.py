"""The names a run's ending by the system is recorded under: the audit
actions, and the reaper's rules.

A module that imports nothing, so the metrics_exporter (which has none of
the app's settings) can import it. The run reaper (domain/services/
run_reaper.py) and RQ's timeout path (workflow_service.mark_run_failed_on_
timeout) write these names into `audit_events`; the exporter emits every
(action, rule) pair below at 0 before its first event (observability PR 32),
so Prometheus's increase() sees the first reap of each rule. A series that
first appears at 1 has no earlier sample to rise from.
"""

ACTION_RUN_REAPED = "workflow_run_reaped"
ACTION_RUN_TIMED_OUT = "workflow_run_timed_out"

# What RQ says about a job that will never move its run on: the verdicts the
# dispatcher's RunJobInspector returns (None means it may still run). Each is
# also the `rule` recorded in the run's workflow_run_reaped audit event.
JOB_FAILED = "failed_registry"
JOB_CANCELED = "canceled"
JOB_FINISHED = "finished"
JOB_UNKNOWN = "unknown"
JOB_ABANDONED = "abandoned"
# The two rules that are not RQ verdicts.
RULE_CAP = "cap"
RULE_DEAD_LETTER = "dead_letter"

REAPER_RULES = (
    JOB_FAILED,
    JOB_CANCELED,
    JOB_FINISHED,
    JOB_UNKNOWN,
    JOB_ABANDONED,
    RULE_CAP,
    RULE_DEAD_LETTER,
)

# Every (action, rule) pair a run's system ending can be counted under. A
# timed-out run's audit row has no rule; the exporter reads it as "".
KNOWN_RUN_ENDINGS: tuple[tuple[str, str], ...] = (
    *((ACTION_RUN_REAPED, rule) for rule in REAPER_RULES),
    (ACTION_RUN_TIMED_OUT, ""),
)
