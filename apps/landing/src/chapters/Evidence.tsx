import { CLAIMS } from "../data/claims";

/** Chapter 4. The claim sheet, reader and trace panel come alive in V3. */
export function Evidence() {
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
              {CLAIMS.map((c) => (
                <li key={c.t}>
                  <button type="button" className="cl" aria-pressed="false">
                    <span className="bx" aria-hidden="true" />
                    <span className="tx">{c.t}</span>
                  </button>
                </li>
              ))}
            </ol>
          </div>
          <div className="reader" id="reader" aria-hidden="true" />
          <div className="trace" id="trace" aria-live="polite" />
          <div className="ctl">
            <button className="ghost" id="reset" type="button">
              Reset
            </button>
            <span id="cstat">0 of {CLAIMS.length} statements reviewed</span>
          </div>
        </div>
      </div>
    </section>
  );
}
