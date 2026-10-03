import { BUYER, TNO, type PageRow } from "../data/tender";
import { Bars, Foot, Top } from "./parts";

/** One page of the tender document. Mandatory pages carry a clause rule and fewer bars. */
export function TenderPage({ p, id }: { p: PageRow; id: number }) {
  const [n, h, t, m] = p;
  return (
    <>
      <Top left={BUYER} tag={`p. ${n}`} />
      <p className="c-ref">Tender {TNO}</p>
      <p className="c-t">{h}</p>
      <p className={m ? "c-p clause" : "c-p"}>{t}</p>
      <Bars n={m ? 6 : 8} s={id} />
      <Foot left={TNO} right={`Page ${n} of 240`} />
    </>
  );
}
