/** Chapter 3. The world shows requirements wired to their evidence from V2. */
export function Eligibility() {
  return (
    <section className="ch" id="match" data-ch="2" data-hold=".18">
      <div className="col">
        <p className="lbl">
          <span>3</span>Eligibility check
        </p>
        <h2>Each criterion assessed against your own evidence.</h2>
        <p className="txt">
          TenderOS searches your records for supporting documents, decides every criterion and cites
          the file behind each decision. Reviewers check a citation instead of repeating the search.
        </p>
        <div className="tally">
          <p className="m">
            <b>4</b>
            <span>Met, each supported by a cited document.</span>
          </p>
          <p className="pt">
            <b>1</b>
            <span>Partially met. Two qualifying similar works against the three required.</span>
          </p>
          <p className="g">
            <b>1</b>
            <span>
              Not met. Clause 11.7 requires subcontractors to be named at bid stage, and no
              subcontractor records exist.
            </span>
          </p>
        </div>
      </div>
    </section>
  );
}
