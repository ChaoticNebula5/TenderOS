const CAUSES = ["ISO 27001 certificate expires", "A. Mehta leaves the team", "Rate schedule revised"];

/** Chapter 5. The cause buttons start the impact wave in V4. */
export function Impact() {
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
            {CAUSES.map((t, j) => (
              <button key={t} type="button" data-s={j} aria-pressed="false">
                {t}
              </button>
            ))}
          </div>
          <div className="fall" id="fall" aria-live="polite">
            <p className="idle">Select a change to trace its impact through the bid.</p>
          </div>
        </div>
      </div>
    </section>
  );
}
