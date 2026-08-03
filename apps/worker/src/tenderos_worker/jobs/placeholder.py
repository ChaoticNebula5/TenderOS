import logging

logger = logging.getLogger(__name__)


def process_outbox_event(event_id: str, aggregate_type: str, event_type: str) -> None:
    """No-op consumer. Real (aggregate_type, event_type) handlers land as later
    blocks give them meaning - mirrors B1's worker already listening on all 5
    queues with nothing real to process yet."""
    logger.info(
        "outbox event dispatched with no real consumer yet: event_id=%s aggregate_type=%s "
        "event_type=%s",
        event_id,
        aggregate_type,
        event_type,
    )
