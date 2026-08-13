from tenderos_backend.orchestration.state import TenderState


async def specialist_join_node(state: TenderState) -> dict:
    """The seam R2's fixed topology converges on after specialist_dispatch's
    parallel fan-out. By the time LangGraph calls this node, every Send()'d
    specialist branch has already merged its completion id into
    completed_task_ids via the operator.add reducer (state.py's Annotated field)
    - LangGraph's own execution model guarantees all fanned-out branches finish
    before a shared downstream node runs, so there's no manual waiting/polling
    here. This is also the insertion point later blocks (B9-B12) extend: their
    real subgraphs slot in between this node and final_gate without this node
    itself changing.

    No sync_run_status call and no "status" key in the return value (B7 fix,
    two rounds). final_gate_node, its unconditional next node, makes the
    exact same "awaiting_final_approval" DB write itself moments later, and
    nothing ever routes on TenderState["status"] == "awaiting_final_approval"
    (only "rejected" is checked, by routing.py) - this node's own copy of
    that write was always redundant. It turned out to be actively harmful,
    not just redundant, once B7 put an interrupt-bearing node
    (compliance_gate_node) upstream of this one in the same un-Send()'d
    continuation: interrupt()'s documented replay-the-whole-function
    behavior means THIS node replays too on every later resume in that
    branch (e.g. approving final_readiness), and an unconditional DB write
    here would silently stomp workflow_runs.status back to
    "waiting_for_human" even after final_gate_node had already advanced it
    to "ready"/"completed" - caught live via a debug harness stepping
    through gate-by-gate, not by an assertion message alone. Removing the
    write here (rather than trying to guard it, which this node has no
    natural "first time" signal to check against, unlike a gate node's own
    persisted row) closes it structurally.
    """
    return {}
