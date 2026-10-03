import { memo } from "react";
import { DR, EV, PAGES, NE, NP, isEv, isPg } from "../data/tender";
import { els } from "../world/store";
import { Draft } from "./Draft";
import { Evidence } from "./Evidence";
import { TenderPage } from "./TenderPage";

/**
 * One document in the world, as three nested layers (architecture rule 4):
 * - `.cw` carries translate, rotate and a fine scale of a few percent, on the GPU.
 * - `.sc` carries the coarse scale, which changes in steps; only a step re-renders text.
 * - `.card` holds the content and is never scaled directly.
 * The glows sit beside the card inside `.sc` and fade by opacity alone (rule 6).
 * Rendered once; the frame loop writes styles through the refs registered here.
 */
export const Doc = memo(function Doc({ id }: { id: number }) {
  return (
    <div
      className="cw"
      ref={(el) => {
        if (el) els.wrap[id] = el;
      }}
    >
      <div
        className="sc"
        ref={(el) => {
          if (el) els.sc[id] = el;
        }}
      >
        <i className="glow ga" />
        <i className="glow gr" />
        <div
          className="card"
          ref={(el) => {
            if (el) els.card[id] = el;
          }}
        >
          {isPg(id) ? (
            <TenderPage p={PAGES[id]} id={id} />
          ) : isEv(id) ? (
            <Evidence e={EV[id - NP]} id={id} />
          ) : (
            <Draft d={DR[id - NP - NE]} id={id} />
          )}
        </div>
      </div>
    </div>
  );
});
