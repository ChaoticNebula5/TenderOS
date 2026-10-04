import { LINKS } from "../data/links";

/** Chapter 1. The world beside it is the overview sphere, which can be dragged. */
export function Overview() {
  return (
    <section className="ch" id="top" data-ch="0">
      <div className="col">
        <h1>
          Every claim in your bid, <i>traced to its source.</i>
        </h1>
        <p className="lead">
          TenderOS reads the tender document and your company records, checks each eligibility
          criterion, and drafts a technical bid in which every statement links to the document that
          supports it.
        </p>
        <div className="row-b">
          <a className="btn p" href={LINKS.heroStart}>
            Start a tender
          </a>
          <a className="btn s" href={LINKS.heroHowItWorks}>
            See how it works
          </a>
        </div>
        <p className="note">
          <span className="h-m">Drag to rotate the documents.</span>
          <span className="h-t">Swipe sideways to rotate the documents.</span>
        </p>
      </div>
    </section>
  );
}
