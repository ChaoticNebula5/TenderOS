import { MAND, PAGES } from "../data/tender";

/** Chapter 2. The index rows become the two-way link with the helix in V2. */
export function Analysis() {
  return (
    <section className="ch pin mfree r" id="pack" data-ch="1" data-hold=".32">
      <div className="stick">
        <div className="col">
          <p className="lbl">
            <span>2</span>Tender analysis
          </p>
          <h2>240 pages read. Six mandatory criteria extracted.</h2>
          <p className="txt">
            Eligibility conditions are spread across the notice inviting tender, annexures,
            corrigenda and pre-bid clarifications. TenderOS reads the full set in sequence and
            records each criterion with its clause and page reference.
          </p>
          <ol className="idx" id="idx" aria-label="Mandatory criteria">
            {MAND.map((i) => (
              <li key={i}>
                <button type="button">
                  <span>p. {PAGES[i][0]}</span>
                  {PAGES[i][4]}
                </button>
              </li>
            ))}
          </ol>
          <p className="note">Select a criterion to bring its page forward.</p>
        </div>
      </div>
    </section>
  );
}
