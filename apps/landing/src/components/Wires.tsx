import { els } from "../world/store";
import { tend, thread, threadGlow } from "../world/thread";
import { MATCH_WIRES, gapWire, impactEdge, matchWire } from "../world/wires";

/**
 * The wire layer, with every path rendered once and empty. Groups: wg holds the base
 * wires (eligibility and impact edges), wh the impact wave's hot wires, wd its pulse
 * dots; the thread paths are the evidence trace. The frame loop writes geometry and opacity.
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
        {impactEdge.map((e, k) => (
          <path
            key={`e${k}`}
            className="w-edge"
            ref={(el) => {
              e.el = el;
            }}
          />
        ))}
      </g>
      <g id="wh">
        {impactEdge.map((e, k) => (
          <path
            key={k}
            className="w-hot"
            style={{ opacity: 0 }}
            ref={(el) => {
              e.hot = el;
            }}
          />
        ))}
      </g>
      <g id="wd">
        {impactEdge.map((e, k) => (
          <circle
            key={k}
            className="pulse"
            r="4"
            style={{ opacity: 0 }}
            ref={(el) => {
              e.dot = el;
            }}
          />
        ))}
      </g>
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
