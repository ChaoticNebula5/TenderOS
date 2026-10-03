import type { Draft as Dr } from "../data/tender";
import { Bars, Foot, Top } from "./parts";

const GATE_ITEMS = [
  "All six mandatory criteria addressed",
  "Pricing arithmetic verified",
  "Review findings resolved",
  "Every statement traced to a source",
  "Approver sign-off",
];

/** A section of the bid draft, or the readiness gate that closes it. */
export function Draft({ d, id }: { d: Dr; id: number }) {
  if (d.gate) {
    return (
      <>
        <Top left="Bid KMRC-14" tag="Gate" />
        <p className="c-t">Readiness checklist</p>
        {GATE_ITEMS.map((t) => (
          <div className="chk" key={t}>
            <i />
            {t}
          </div>
        ))}
        <p className="stamp">
          <span className="a">Clear to submit</span>
          <span className="b">Blocked</span>
        </p>
      </>
    );
  }
  return (
    <>
      <Top left="Draft, version 3" tag={d.k} />
      <p className="c-t">{d.t}</p>
      <p className="c-p">{d.p}</p>
      <Bars n={9} s={id + 40} />
      <Foot left="Bid KMRC-14" right="Draft 3" />
    </>
  );
}
