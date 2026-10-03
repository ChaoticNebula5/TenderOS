/** Chapter 6. The world beside it is the submission stack from V5. */
export function Submission() {
  return (
    <section className="ch r" id="bid" data-ch="5">
      <div className="col">
        <p className="lbl">
          <span>6</span>Submission
        </p>
        <h2>
          Submit a bid you can <i>defend clause by clause.</i>
        </h2>
        <p className="txt">
          Seven specialised agents work from a single shared state, with human approval at every
          gate. The submitted bid keeps a complete audit trail from each statement to its source.
        </p>
        <div className="row-b">
          <a className="btn p" href="#top">
            Start a tender
          </a>
          <a className="btn s" href="#top">
            Read the architecture
          </a>
        </div>
        <p className="note">Built with LangGraph, Gemini, PostgreSQL and Neo4j.</p>
      </div>
    </section>
  );
}
