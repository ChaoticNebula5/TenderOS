import { useEffect, useRef, useState } from "react";
import { CLAIMS, type Claim } from "../data/claims";
import { EV, NP } from "../data/tender";
import { Evidence as EvidenceDoc } from "../documents/Evidence";
import { CH, CW } from "../world/math";
import { clearClaims, selectClaim } from "../world/actions";
import { els } from "../world/store";

const verdict = (c: Claim) => ("src" in c ? "ok" : c.kind);

/** The trace panel for a selected statement: three hops to the source, or the reason it has none. */
function Trace({ c }: { c: Claim }) {
  if ("src" in c) {
    return (
      <>
        <p className="kick">
          <i />
          Supported. Traced to source in three steps.
        </p>
        <p className="hop">
          <span>Knowledge item</span>
          <b>{c.k}</b>
        </p>
        <p className="hop">
          <span>Document extract</span>
          <b>{c.c}</b>
        </p>
        <p className="hop">
          <span>Source file</span>
          <b>{c.d}</b>
        </p>
      </>
    );
  }
  return c.kind === "flag" ? (
    <>
      <p className="kick flag">
        <i />
        Commitment. Flagged for approval.
      </p>
      <p className="why">{c.why}</p>
    </>
  ) : (
    <>
      <p className="kick bad">
        <i />
        Unsupported. No evidence found.
      </p>
      <p className="why">{c.why}</p>
    </>
  );
}

/**
 * The cited document, inline below the sheet at full column width. Phone only: there
 * the world is too small to read from, so the document comes to the text instead.
 */
function Reader({ id, width }: { id: number; width: number }) {
  const rs = Math.min(1, width / CW);
  return (
    <div data-s="lit">
      <div
        className="card"
        style={{ transform: `translate(${((width - CW * rs) / 2).toFixed(1)}px,0) scale(${rs.toFixed(4)})` }}
      >
        <EvidenceDoc e={EV[id - NP]} id={id} />
      </div>
    </div>
  );
}

/**
 * Chapter 4. Selecting a statement marks it, keeps its verdict mark, fills the trace
 * panel and counter (React state: they change on click only), and tells the world
 * through selectClaim(), which the frame loop reads to move the cited document and
 * draw the thread.
 */
export function Evidence() {
  const [sel, setSel] = useState(-1);
  const [checked, setChecked] = useState<ReadonlySet<number>>(() => new Set());
  const readerRef = useRef<HTMLDivElement>(null);
  // reader width at phone size, 0 on larger screens; re-read on resize only
  const [readerW, setReaderW] = useState(0);

  useEffect(() => {
    const update = () => {
      const el = readerRef.current;
      setReaderW(innerWidth <= 760 && el ? el.clientWidth : 0);
    };
    update();
    addEventListener("resize", update);
    return () => removeEventListener("resize", update);
  }, []);

  const select = (i: number) => {
    selectClaim(i);
    setSel(i);
    setChecked((prev) => (prev.has(i) ? prev : new Set(prev).add(i)));
  };
  const reset = () => {
    clearClaims();
    setSel(-1);
    setChecked(new Set());
  };

  const c = sel >= 0 ? CLAIMS[sel] : null;
  const src = c && "src" in c ? c.src : -1;
  const showReader = readerW > 0 && src >= 0;
  const n = checked.size,
    total = CLAIMS.length;

  return (
    <section className="ch pin mfree r" id="proof" data-ch="3" data-hold=".42">
      <div className="stick">
        <div className="col">
          <p className="lbl">
            <span>4</span>Evidence tracing
          </p>
          <h2>Select a statement to see the evidence behind it.</h2>
          <p className="txt">
            Every sentence in the draft resolves to a knowledge item, a document extract and a source
            file. Statements without support are flagged before the bid is submitted.
          </p>
          <div className="sheet">
            <p className="sh">Technical bid, Section 4: Capability and experience</p>
            <ol className="cls" id="claims">
              {CLAIMS.map((cl, i) => (
                <li key={cl.t}>
                  <button
                    type="button"
                    className={`cl${checked.has(i) ? " " + verdict(cl) : ""}${i === sel ? " on" : ""}`}
                    aria-pressed={i === sel}
                    onClick={() => select(i)}
                    ref={(el) => {
                      if (el) els.claimBtn[i] = el;
                    }}
                  >
                    <span className="bx" aria-hidden="true" />
                    <span className="tx">{cl.t}</span>
                  </button>
                </li>
              ))}
            </ol>
          </div>
          <div
            className="reader"
            id="reader"
            aria-hidden="true"
            ref={readerRef}
            style={{ height: showReader ? `${(CH * Math.min(1, readerW / CW)).toFixed(0)}px` : "0px" }}
          >
            {showReader && <Reader id={src} width={readerW} />}
          </div>
          <div className="trace" id="trace" aria-live="polite">
            {c && <Trace c={c} />}
          </div>
          <div className="ctl">
            <button className="ghost" id="reset" type="button" onClick={reset}>
              Reset
            </button>
            <span id="cstat" aria-live="polite">
              {n < total
                ? `${n} of ${total} statements reviewed`
                : "All five reviewed. One statement is unsupported and one is a commitment that needs approval. Both are flagged before submission."}
            </span>
          </div>
        </div>
      </div>
    </section>
  );
}
