import { MAND, PAGES } from "../data/tender";
import { bringToFront } from "../world/actions";
import { els, input } from "../world/store";

const hover = (j: number) => () => {
  input.idxHover = j;
};
const unhover = () => {
  input.idxHover = -1;
};

/**
 * Chapter 2. The index rows and the highlighted helix pages point at each other:
 * hovering or focusing a row lights its page, and selecting it spins the page to
 * the front. The frame loop marks the row (`.on`); nothing here sets React state.
 */
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
            {MAND.map((i, j) => (
              <li key={i}>
                <button
                  type="button"
                  ref={(el) => {
                    if (el) els.idxBtn[j] = el;
                  }}
                  onMouseEnter={hover(j)}
                  onMouseLeave={unhover}
                  onFocus={hover(j)}
                  onBlur={unhover}
                  onClick={() => bringToFront(i)}
                >
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
