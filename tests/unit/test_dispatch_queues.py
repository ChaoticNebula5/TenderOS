"""Phase 4 Stage 2: which RQ queue each outbox event's job goes to.

OperationRequested moved to its own "operations" queue, served by its own
worker process (operations_worker), so an operation a person is waiting on
never queues behind a workflow job. Every other event must stay on the queue
it used before. The expected table below is written out by hand on purpose:
a route that silently changes queue fails here. No database or Redis: the
routes only read the event's payload.
"""

import uuid

import pytest
from redis import Redis
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.domain.events.outbox_events import (
    AGGREGATE_APPROVAL_GATE,
    AGGREGATE_ASYNC_OPERATION,
    AGGREGATE_DOCUMENT,
    AGGREGATE_KNOWLEDGE_SELECTION,
    AGGREGATE_REQUIREMENT,
    AGGREGATE_WORKFLOW_RUN,
    EVENT_DOCUMENT_UPLOADED,
    EVENT_GATE_DECISION_RECORDED,
    EVENT_KNOWLEDGE_DOCUMENT_UPLOADED,
    EVENT_KNOWLEDGE_SELECTION_CHANGED,
    EVENT_OPERATION_REQUESTED,
    EVENT_REQUIREMENT_VERIFICATION_REQUESTED,
    EVENT_WORKFLOW_RUN_CREATED,
)
from tenderos_worker import outbox_dispatcher
from tenderos_worker.worker import OPERATIONS_QUEUE, QUEUE_NAMES, queue_names_from

# Every key any route reads; each route ignores the ones it does not need.
_PAYLOAD = {
    "workflow_run_id": str(uuid.uuid4()),
    "gate_id": str(uuid.uuid4()),
    "document_id": str(uuid.uuid4()),
    "batch_id": str(uuid.uuid4()),
    "requirement_id": str(uuid.uuid4()),
    "tender_id": str(uuid.uuid4()),
    "knowledge_item_id": str(uuid.uuid4()),
    "state_version": 3,
    "operation_id": str(uuid.uuid4()),
    "kind": "proposal_generation",
}

_EXPECTED_QUEUES = {
    (AGGREGATE_WORKFLOW_RUN, EVENT_WORKFLOW_RUN_CREATED): "workflow",
    (AGGREGATE_APPROVAL_GATE, EVENT_GATE_DECISION_RECORDED): "workflow",
    (AGGREGATE_DOCUMENT, EVENT_DOCUMENT_UPLOADED): "ingestion",
    (AGGREGATE_DOCUMENT, EVENT_KNOWLEDGE_DOCUMENT_UPLOADED): "workflow",
    (AGGREGATE_REQUIREMENT, EVENT_REQUIREMENT_VERIFICATION_REQUESTED): "workflow",
    (AGGREGATE_KNOWLEDGE_SELECTION, EVENT_KNOWLEDGE_SELECTION_CHANGED): "workflow",
    (AGGREGATE_ASYNC_OPERATION, EVENT_OPERATION_REQUESTED): "operations",
}


def _event(aggregate_type: str, event_type: str) -> OutboxEvent:
    return OutboxEvent(
        id=uuid.uuid4(),
        aggregate_type=aggregate_type,
        aggregate_id=uuid.uuid4(),
        event_type=event_type,
        payload=dict(_PAYLOAD),
        idempotency_key=uuid.uuid4().hex,
    )


def _queue_for(aggregate_type: str, event_type: str) -> str:
    build = outbox_dispatcher._ROUTES.get(
        (aggregate_type, event_type), outbox_dispatcher._default_dispatch
    )
    queue_name, _job_fn, _args, _job_id = build(_event(aggregate_type, event_type))
    return queue_name


def test_every_route_is_in_the_expected_table():
    # A new route must be added to _EXPECTED_QUEUES deliberately.
    assert set(outbox_dispatcher._ROUTES) == set(_EXPECTED_QUEUES)


@pytest.mark.parametrize(("key", "queue_name"), sorted(_EXPECTED_QUEUES.items()))
def test_each_event_goes_to_its_queue(key, queue_name):
    assert _queue_for(*key) == queue_name


def test_operation_requested_goes_to_the_operations_queue_and_nothing_else_does():
    assert OPERATIONS_QUEUE == "operations"
    assert _queue_for(AGGREGATE_ASYNC_OPERATION, EVENT_OPERATION_REQUESTED) == OPERATIONS_QUEUE
    others = {q for key, q in _EXPECTED_QUEUES.items() if key[1] != EVENT_OPERATION_REQUESTED}
    assert OPERATIONS_QUEUE not in others
    # An event with no route of its own still falls back to maintenance.
    assert _queue_for("tender", "TenderCreated") == "maintenance"


def test_the_dispatcher_can_enqueue_on_every_queue_a_route_names():
    # redis-py connects lazily: building the queues opens no connection.
    queues = outbox_dispatcher._queues(Redis())
    assert set(_EXPECTED_QUEUES.values()) | {"maintenance"} <= set(queues)


def test_the_default_worker_does_not_listen_to_operations_and_the_operations_worker_only_does():
    assert OPERATIONS_QUEUE not in QUEUE_NAMES
    assert queue_names_from([]) == QUEUE_NAMES
    assert queue_names_from(["operations"]) == ["operations"]
    with pytest.raises(SystemExit):
        queue_names_from(["operatoins"])
