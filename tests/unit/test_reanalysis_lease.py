"""Plan item 18: the reanalysis claim's lease outlives the job it guards.

The lease is written as a number in the backend (which cannot import the
worker), so this pins it to the workflow queue's RQ job timeout plus the
operations' 60 s margin. If the timeout grows and the lease does not, a
claim could lapse while its job is still running, and a redelivery could
take it over and repeat the work.
"""

from datetime import timedelta

from tenderos_backend.domain.services.operation_service import LEASE_MARGIN
from tenderos_backend.domain.services.reanalysis_service import (
    REANALYSIS_LEASE,
    STALE_PREVIEW_WAITS_SECONDS,
)
from tenderos_worker.jobs.workflow import WORKFLOW_JOB_TIMEOUT_SECONDS


def test_the_reanalysis_lease_is_the_workflow_job_timeout_plus_the_margin():
    assert REANALYSIS_LEASE == timedelta(seconds=WORKFLOW_JOB_TIMEOUT_SECONDS) + LEASE_MARGIN


def test_the_stale_preview_waits_are_the_decided_ones_and_fit_well_inside_the_timeout():
    """Plan item 124 (user decision Q1): about 2, 5, 10 and 20 s between five
    attempts, about 40 s in all, sized to outlast a person's burst of
    changes; a twentieth of the job's timeout at most, so the job's own
    work keeps nearly all of it."""
    assert STALE_PREVIEW_WAITS_SECONDS == (2.0, 5.0, 10.0, 20.0)
    assert sum(STALE_PREVIEW_WAITS_SECONDS) <= WORKFLOW_JOB_TIMEOUT_SECONDS / 20
