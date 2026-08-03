import uuid

# TenderCreated is a B2 engineering choice, not a literal Build Guide requirement -
# Part G2's text only mandates an outbox event for document upload (a later block).
# This gives the outbox mechanism a real trigger ahead of any real consumer.
EVENT_TENDER_CREATED = "TenderCreated"

AGGREGATE_TENDER = "tender"


def tender_created_payload(tender_id: uuid.UUID, organization_id: uuid.UUID, name: str) -> dict:
    return {
        "tender_id": str(tender_id),
        "organization_id": str(organization_id),
        "name": name,
    }


def tender_created_idempotency_key(tender_id: uuid.UUID) -> str:
    return f"tender_created:{tender_id}"
