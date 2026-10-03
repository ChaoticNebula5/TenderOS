import { N } from "../data/tender";
import { Doc } from "../documents/Doc";
import { els } from "../world/store";
import { Labels } from "./Labels";
import { Wires } from "./Wires";

const IDS = Array.from({ length: N }, (_, i) => i);

/**
 * The fixed layers behind the chapters, in the reference's stacking order: the world
 * (atmosphere and documents), the two scrims, labels, wires, and the layer thrown
 * pages fly in. All decorative to assistive tech; the chapters carry the content.
 */
export function WorldLayers() {
  return (
    <>
      <div id="world" aria-hidden="true">
        <div
          id="atmo"
          ref={(el) => {
            els.atmo = el;
          }}
        />
        <div id="cards">
          {IDS.map((i) => (
            <Doc key={i} id={i} />
          ))}
        </div>
      </div>
      <div
        className="scrim"
        id="scrimL"
        aria-hidden="true"
        ref={(el) => {
          els.scrimL = el;
        }}
      />
      <div
        className="scrim"
        id="scrimR"
        aria-hidden="true"
        ref={(el) => {
          els.scrimR = el;
        }}
      />
      <Labels />
      <Wires />
      <div id="flying" aria-hidden="true" />
    </>
  );
}
