import { els } from "../world/store";
import { tend, thread, threadGlow } from "../world/thread";
import { MATCH_WIRES, gapWire, matchWire } from "../world/wires";

/**
 * The wire layer, with every path rendered once and empty. Groups: wg holds the base
 * wires, wh the impact highlights (V4), wd the pulse dots (V4); the thread paths are
 * the evidence trace (V3). The frame loop writes geometry and opacity.
 */
export function Wires() {
  return (
    <svg
      id="wires"
      aria-hidden="true"
      ref={(el) => {
        els.wires = el;
      }}
    >
      <g id="wg">
        {MATCH_WIRES.map((m, k) => (
          <path
            key={k}
            className={m.st === "part" ? "w-part" : "w-met"}
            ref={(el) => {
              matchWire[k].el = el;
            }}
          />
        ))}
        <path
          className="w-gap"
          ref={(el) => {
            gapWire.el = el;
          }}
        />
      </g>
      <g id="wh" />
      <g id="wd" />
      <path
        id="threadGlow"
        ref={(el) => {
          threadGlow.el = el;
        }}
      />
      <path
        id="thread"
        ref={(el) => {
          thread.el = el;
        }}
      />
      <circle
        id="tend"
        r="5"
        ref={(el) => {
          tend.el = el;
        }}
      />
    </svg>
  );
}
