import type { Dispatch, SetStateAction } from "react";
import { getGate } from "../api/client";
import type { GateResponse } from "../api/client";

// Plan item 73, decision 3: a pending gate's live counts follow the rows a
// reviewer changes on the same screen (approving an entry, picking a
// candidate, resolving a finding). Each screen calls this after such a
// change; it re-reads the gate, whose response carries the counts as they
// are now.
//
// Applied only while the screen still shows that same gate as pending: a
// decision made meanwhile has already replaced it with the decided gate,
// and a late answer must not put the pending one back. A failed read keeps
// the counts already shown; the next load reads them again.
export async function refreshPendingGate<G extends GateResponse>(
  gate: G | null,
  setGate: Dispatch<SetStateAction<G | null>>,
): Promise<void> {
  if (gate === null || gate.status !== "pending") return;
  let fresh: GateResponse;
  try {
    fresh = await getGate(gate.gate_id);
  } catch {
    return;
  }
  setGate((current) =>
    current !== null && current.gate_id === fresh.gate_id && current.status === "pending"
      ? (fresh as G)
      : current,
  );
}
