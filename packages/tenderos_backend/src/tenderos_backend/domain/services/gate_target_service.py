import logging
import uuid

from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services.commercial_service import (
    resolve_pricing_finding,
    resolve_pricing_finding_in_uow,
)
from tenderos_backend.domain.services.compliance_service import (
    approve_compliance_entry,
    approve_compliance_entry_in_uow,
)
from tenderos_backend.domain.services.talent_service import (
    select_candidate,
    select_candidate_in_uow,
)

logger = logging.getLogger(__name__)

# Q1's chosen target_type set - role_match, not role_requirement, because
# select_candidate needs the specific candidate row (a role_requirement can
# have several role_matches); compliance_entry/pricing_finding are already
# the directly-actionable id their own gate's `targets` list already uses.
_KNOWN_TARGET_TYPES = frozenset({"compliance_entry", "role_match", "pricing_finding"})


class TargetOwnershipError(Exception):
    """Raised when a target_update's target_id does not belong to the
    gate's own tender - see apply_target_update's own docstring for why
    this must be a hard error, not a skip."""

    def __init__(self, target_type: str, target_id: uuid.UUID) -> None:
        self.target_type = target_type
        self.target_id = target_id
        super().__init__(f"{target_type} {target_id} does not belong to this gate's tender")


async def _owning_tender_id_in_uow(
    uow: UnitOfWork, target_type: str, target_id: uuid.UUID
) -> uuid.UUID | None:
    """One-hop-or-two ownership lookup per target_type, same shape as
    dependencies.py's own _organization_id_for_* helpers - mechanical, not
    the judgment call below. Returns None if the target_id doesn't exist
    at all (a separate case from "exists but wrong tender"). Takes an
    already-open uow so apply_target_updates can run every lookup in the
    same transaction as the writes that follow.
    """
    if target_type == "compliance_entry":
        entry = await uow.compliance.get_compliance_entry(target_id)
        if entry is None:
            return None
        requirement = await uow.requirements.get(entry.requirement_id)
        return requirement.tender_id if requirement else None
    if target_type == "role_match":
        match = await uow.talent.get_role_match(target_id)
        if match is None:
            return None
        role = await uow.talent.get_role_requirement(match.role_requirement_id)
        return role.tender_id if role else None
    if target_type == "pricing_finding":
        finding = await uow.commercial.get_pricing_finding(target_id)
        return finding.tender_id if finding else None
    return None


async def _owning_tender_id(target_type: str, target_id: uuid.UUID) -> uuid.UUID | None:
    """Standalone variant - opens its own uow. Used by apply_target_update
    (the standalone, self-committing single-entry function below); the
    batch coordinator (apply_target_updates) uses _owning_tender_id_in_uow
    directly instead, so its own lookups share the one transaction the
    batch's writes run in."""
    async with UnitOfWork() as uow:
        return await _owning_tender_id_in_uow(uow, target_type, target_id)


# TODO(human): apply_target_update
#
# Steps (plain-English):
#   1. Read target_type, target_id, decision out of the target_update dict.
#      If target_type isn't one of _KNOWN_TARGET_TYPES, or decision isn't
#      "approve", do nothing and return - forward-compatible, permissive on
#      unrecognized VALUES (same posture map_finding_to_corrective_task
#      already takes on unknown finding categories).
#   2. Call _owning_tender_id(target_type, target_id) to find which tender
#      this target id actually belongs to.
#   3. If it returns None (the id doesn't exist at all) OR it doesn't equal
#      gate_tender_id (the gate's own tender, passed in by the caller),
#      raise TargetOwnershipError(target_type, target_id) - a HARD reject,
#      not a skip. This is the real security boundary the plan's Q4
#      corrected: target_updates comes from an untrusted request body, and
#      a caller sending a foreign id is either confused or probing -
#      neither deserves a quiet no-op that tells them nothing happened
#      without confirming whether their probe landed.
#   4. If ownership checks out, dispatch to the ONE matching existing,
#      already-tested, already-audited service call - no new business
#      logic, just routing to the right one:
#        - "compliance_entry" -> approve_compliance_entry(target_id, actor_user_id)
#        - "role_match"       -> select_candidate(target_id, actor_user_id)
#        - "pricing_finding"  -> resolve_pricing_finding(target_id, actor_user_id)
#      Each of these already opens and commits its own UnitOfWork
#      internally (the standing convention this codebase follows) - don't
#      wrap them in another one here.
#
# What's already available to use:
#   - `gate_tender_id: uuid.UUID` and `target_update: dict` - the two
#     parameters this function is called with.
#   - `actor_user_id: uuid.UUID` - the caller (current_user.id from the route).
#   - `_owning_tender_id`, `TargetOwnershipError`, `_KNOWN_TARGET_TYPES` -
#     all defined just above in this file.
#   - `approve_compliance_entry`, `select_candidate`, `resolve_pricing_finding`
#     - imported at the top of this file, each `(id: uuid.UUID, actor_user_id: uuid.UUID)`.
#
# Why this is worth writing by hand: it's the one real judgment call left
# in this block - correctly splitting "permissive" (unknown target_type/
# decision values, forward-compatibility) from "strict" (ownership,
# always a hard reject) into two genuinely different policies rather than
# one blanket posture. Getting the split backwards either direction has a
# real cost: too permissive on ownership silently reproduces the exact
# select_candidate cross-tenant bug this codebase already found and fixed
# once; too strict on unknown values means a client can never safely add a
# new decision type without breaking every OLDER gate-decision call that
# doesn't know about it yet.
async def apply_target_update(
    gate_tender_id: uuid.UUID, target_update: dict, actor_user_id: uuid.UUID
) -> None:
    target_type = target_update.get("target_type")
    decision = target_update.get("decision")
    
    if target_type not in _KNOWN_TARGET_TYPES or decision != "approve":
        return
    target_id = uuid.UUID(str(target_update["target_id"]))
    
    owning_tender_id  = await _owning_tender_id(target_type, target_id)
    if owning_tender_id is None or owning_tender_id != gate_tender_id:
        raise TargetOwnershipError(target_type, target_id)

    if target_type == "compliance_entry":
        await approve_compliance_entry(target_id, actor_user_id)
    elif target_type == "role_match":
        await select_candidate(target_id, actor_user_id)
    elif target_type == "pricing_finding":
        await resolve_pricing_finding(target_id, actor_user_id)


async def apply_target_updates(
    gate_tender_id: uuid.UUID, target_updates: list[dict], actor_user_id: uuid.UUID
) -> None:
    """The real entry point (routes/gates.py's decide_gate calls this, never
    apply_target_update directly) - so this is where atomicity actually has
    to hold. Runs the WHOLE batch - every ownership check and every write -
    inside one shared UnitOfWork, committed exactly once at the end.

    This closes a gap the previous two-pass design (validate-all-then-
    apply-all, each apply self-committing) still had: that design correctly
    stopped an ownership failure from letting anything commit, but did
    nothing about a LATER dispatch failing for an unrelated reason (e.g. a
    row deleted between validation and dispatch, a real DB error) after an
    EARLIER dispatch in the same batch had already committed on its own.
    With one shared uow, any exception anywhere in the loop below propagates
    out of this function, UnitOfWork.__aexit__ rolls the whole transaction
    back (see its own `if exc_type is not None: await self.session.
    rollback()`), and uow.commit() below - the only commit in this
    function - is never reached. Either everything in the batch lands, or
    nothing does.
    """
    async with UnitOfWork() as uow:
        for target_update in target_updates:
            target_type = target_update.get("target_type")
            decision = target_update.get("decision")
            if target_type not in _KNOWN_TARGET_TYPES or decision != "approve":
                continue
            target_id = uuid.UUID(str(target_update["target_id"]))
            owning_tender_id = await _owning_tender_id_in_uow(uow, target_type, target_id)
            if owning_tender_id is None or owning_tender_id != gate_tender_id:
                raise TargetOwnershipError(target_type, target_id)
            # On the success path too, not just the reject path - this is
            # the log line that lets a live run confirm the ownership check
            # actually executed against real data, rather than the caller
            # having to infer it from the absence of a 403. (Currently
            # dropped by both apps' own missing logging.basicConfig() -
            # see KNOWN_LIMITATIONS.md's dedicated entry - kept here anyway
            # since the call itself is correct and costs nothing.)
            logger.info(
                "target ownership verified: type=%s id=%s tender=%s",
                target_type, target_id, owning_tender_id,
            )

            if target_type == "compliance_entry":
                await approve_compliance_entry_in_uow(uow, target_id, actor_user_id)
            elif target_type == "role_match":
                await select_candidate_in_uow(uow, target_id, actor_user_id)
            elif target_type == "pricing_finding":
                await resolve_pricing_finding_in_uow(uow, target_id, actor_user_id)

        await uow.commit()
