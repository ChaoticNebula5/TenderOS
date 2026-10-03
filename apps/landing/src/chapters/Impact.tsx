import { Fragment, useState } from "react";
import { CAUSES, GATE, NODES, UNREACHED, WAVE } from "../data/impact";
import { seedImpact } from "../world/actions";
import { RM } from "../world/math";

/** Each summary line appears as the wave reaches that step: source, step 1, 2, 3, total. */
const DELAYS = [0.05, 1 / WAVE, 2 / WAVE, 3 / WAVE, 3 / WAVE + 0.35];
const at = (k: number) => (RM ? undefined : { animationDelay: `${DELAYS[k].toFixed(2)}s` });
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** The impact summary for one cause, built from the same hops the wave uses. */
function Summary({ k, hop }: { k: number; hop: number[] }) {
  const s = CAUSES[k].node;
  const atHop = (d: number) => NODES.filter((_, j) => hop[j] === d).map((n) => n.name);
  const r1 = atHop(1),
    r2 = atHop(2),
    gate = hop[GATE] < UNREACHED,
    n = hop.filter((h) => h > 0 && h < UNREACHED).length;
  return (
    <>
      <p style={at(0)}>
        <span>Source</span>
        <span>
          <b>{NODES[s].name}</b> {CAUSES[k].verb}
        </span>
      </p>
      <p style={at(1)}>
        <span>Step 1</span>
        <span>
          <b>
            {r1.length} requirement{r1.length > 1 ? "s" : ""}
          </b>{" "}
          lose{r1.length > 1 ? "" : "s"} supporting evidence: {cap(r1.join(", "))}
        </span>
      </p>
      <p style={at(2)}>
        <span>Step 2</span>
        <span>
          <b>
            {r2.length} proposal section{r2.length > 1 ? "s" : ""}
          </b>{" "}
          contain{r2.length > 1 ? "" : "s"} affected statements: {r2.join(", ")}
        </span>
      </p>
      {gate && (
        <p className="bad" style={at(3)}>
          <span>Step 3</span>
          <span>
            <b>Submission is blocked</b> at the readiness gate until these are resolved.
          </span>
        </p>
      )}
      <p className="tot" style={at(4)}>
        <em>{n}</em> dependent items affected
      </p>
    </>
  );
}

/**
 * Chapter 5. A cause button seeds the impact wave: the hops are computed once, written
 * to the store for the frame loop and kept here for the summary (React state, which
 * changes on click only). Every click restarts the summary's staggered entrance, in step
 * with the wave restarting.
 */
export function Impact() {
  const [run, setRun] = useState<{ k: number; hop: number[]; n: number }>({ k: -1, hop: [], n: 0 });

  const choose = (k: number) => {
    const hop = seedImpact(k);
    setRun((r) => ({ k, hop, n: r.n + 1 }));
  };

  return (
    <section className="ch pin mfree" id="stress" data-ch="4" data-hold=".42">
      <div className="stick">
        <div className="col">
          <p className="lbl">
            <span>5</span>Change impact
          </p>
          <h2>Know what a change affects before you submit.</h2>
          <p className="txt">
            Each bid is stored as a dependency graph linking source documents, requirements and
            proposal sections. When a certificate expires or a team member leaves, TenderOS
            identifies every affected section in seconds.
          </p>
          <div className="causes" id="causes">
            {CAUSES.map((c, k) => (
              <button key={c.button} type="button" data-s={k} aria-pressed={k === run.k} onClick={() => choose(k)}>
                {c.button}
              </button>
            ))}
          </div>
          <div className="fall" id="fall" aria-live="polite">
            {run.k < 0 ? (
              <p className="idle">Select a change to trace its impact through the bid.</p>
            ) : (
              // a new key per click remounts the lines, so their entrance animation replays
              <Fragment key={run.n}>
                <Summary k={run.k} hop={run.hop} />
              </Fragment>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
