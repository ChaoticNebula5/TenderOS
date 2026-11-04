"""Plan item 18: the reanalysis claim's lease outlives the job it guards.

The lease is written as a number in the backend (which cannot import the
worker), so this pins it to the workflow queue's RQ job timeout plus the
operations' 60 s margin. If the timeout grows and the lease does not, a
claim could lapse while its job is still running, and a redelivery could
take it over and repeat the work.
"""

from datetime import timedelta

from tenderos_backend.domain.services.operation_service import LEASE_MARGIN
from tenderos_backend.domain.services.reanalysis_service import REANALYSIS_LEASE
from tenderos_worker.jobs.workflow import WORKFLOW_JOB_TIMEOUT_SECONDS


def test_the_reanalysis_lease_is_the_workflow_job_timeout_plus_the_margin():
    assert REANALYSIS_LEASE == timedelta(seconds=WORKFLOW_JOB_TIMEOUT_SECONDS) + LEASE_MARGIN
